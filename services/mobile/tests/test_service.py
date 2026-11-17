"""Service boundary tests with mocked LLM/build/tester; no live infrastructure."""
import asyncio
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

SERVICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE))
sys.path.insert(0, str(SERVICE.parents[1] / 'common'))
os.environ.setdefault('AGENT_ROLE', 'MOBILE')
os.environ.setdefault('LLM_URL', 'http://example.test/v1')
import main
import mobile_agent as engine
from generation_utils import GenerationError
from test_mobile_agent import DESIGN, TASK


class DependencyTests(unittest.TestCase):
    def test_cycle_unknown_and_missing_dependency_rejected(self):
        for kind in ('cycle', 'unknown', 'missing'):
            design = copy.deepcopy(DESIGN)
            if kind == 'cycle':
                design['files'][1]['depends_on'] = [design['files'][0]['path']]
            elif kind == 'unknown':
                design['files'][1]['depends_on'] = ['not-in-project.kt']
            else:
                del design['files'][1]['depends_on']
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                engine._validate_design(json.dumps(design), TASK['context'])

    def test_dependency_layers(self):
        layers = engine._layers(DESIGN['files'])
        done = set()
        for layer in layers:
            self.assertTrue(all(set(f['depends_on']) <= done for f in layer))
            done.update(f['path'] for f in layer)
        self.assertEqual(len(done), 4)


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.root = Path(self.tmp.name)
        self.patch = patch.object(main, 'ROOT', self.root)
        self.patch.start()
        main.lock = asyncio.Lock()
        self.data = {'run_id':str(uuid4()), 'task':'build Android', 'context':{'goal':'demo'},
                     'contract':{'openapi':{'openapi':'3.0.3','paths':{}}}}
        self.env = patch.dict(os.environ, {'TESTER_URL':'http://tester:8000','MAX_REPAIR_ATTEMPTS':'2'})
        self.env.start()

    async def asyncTearDown(self):
        self.env.stop()
        self.patch.stop()
        self.tmp.cleanup()

    async def test_pass_requires_tester_and_preserves_contract(self):
        result = {'files':{'README.md':'built'},'tokens':7}
        with patch.object(engine, 'mobile_agent', AsyncMock(return_value=result)) as generate, \
             patch.object(engine, 'COMPILE', True), \
             patch.object(main.android_build, 'check_environment'), \
             patch.object(main, 'request', AsyncMock(return_value={'status':'passed','zip_path':'approved.zip','tokens':2})) as qa:
            output = await main.work(self.data)
        self.assertEqual(output['status'], 'passed')
        self.assertEqual(output['zip_path'], 'approved.zip')
        self.assertEqual(output['tokens'], 9)
        context = json.loads(generate.await_args.args[0]['context'])
        self.assertEqual(context['contract'], self.data['contract'])
        self.assertEqual(qa.await_args.args[0], 'http://tester:8000/validate')
        self.assertTrue(qa.await_args.args[1]['generation']['build']['passed'])
        self.assertEqual(output['contract_hash'], main.digest(self.data['contract']))

    async def test_no_kvm_finishes_without_repair(self):
        verdict = {'status':'skipped','skip_reason':'no_kvm','message':'Нет KVM', 'zip_path':'mobile.zip'}
        with patch.object(main, 'generate', AsyncMock(return_value={'status':'ok','tokens':0,'build':{'passed':True}})) as gen, \
             patch.object(main, 'request', AsyncMock(return_value=verdict)):
            output = await main.work(self.data)
        self.assertEqual(output['status'], 'skipped')
        self.assertEqual(output['skip_reason'], 'no_kvm')
        self.assertEqual(output['zip_path'], 'mobile.zip')
        self.assertEqual(gen.await_count, 1)

    async def test_failed_build_never_calls_tester(self):
        for error in (GenerationError('MOBILE build','compile error',5,'MOBILE'),
                      main.android_build.BuildInfraError('no JDK')):
            with patch.object(main, 'generate', AsyncMock(side_effect=error)) as gen, \
                 patch.object(main, 'request', AsyncMock()) as qa:
                output = await main.work(self.data)
            self.assertEqual(output['status'], 'failed')
            self.assertEqual(gen.await_count, 1)
            qa.assert_not_awaited()

    async def test_tester_feedback_reaches_regeneration(self):
        responses = [{'status':'failed','feedback':{'errors':[{'file':'README.md','message':'missing screen'}]}},
                     {'status':'passed','zip_path':'approved.zip'}]
        with patch.object(main, 'generate', AsyncMock(return_value={'status':'ok','tokens':0})) as gen, \
             patch.object(main, 'request', AsyncMock(side_effect=responses)):
            output = await main.work(self.data)
        self.assertEqual(output['attempts'], 2)
        self.assertIn('missing screen', gen.await_args_list[1].args[0])

    async def test_regeneration_removes_stale_files(self):
        folder = self.root/'mobile'; folder.mkdir(); (folder/'old.kt').write_text('old')
        with patch.object(engine, 'mobile_agent', AsyncMock(return_value={'files':{'new.kt':'new'},'tokens':0})), \
             patch.object(engine, 'COMPILE', True), patch.object(main.android_build, 'check_environment'):
            await main.generate('task', {}, folder)
        self.assertFalse((folder/'old.kt').exists())
        self.assertTrue((folder/'new.kt').exists())

    def test_health_and_routes(self):
        self.assertEqual(main.health(), {'status':'ok','role':'MOBILE'})
        self.assertEqual(set(main.app.openapi()['paths']), {'/health','/work'})
