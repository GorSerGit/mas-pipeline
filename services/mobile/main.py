import asyncio
import json
import os
import shutil
from pathlib import Path
from uuid import UUID
import httpx
import android_build
from generation_utils import GenerationError
from fastapi import FastAPI, HTTPException
from common import ROOT, archive, contained, digest, request, save

ROLE=os.environ['AGENT_ROLE']
app=FastAPI(title=ROLE+' agent')
lock=asyncio.Lock()  # original modules keep global usage/debug state

@app.get('/health')
def health():
    return {'status':'ok','role':ROLE}

async def generate(task, context, folder):
    if ROLE == 'MOBILE':
        from mobile_agent import mobile_agent, COMPILE
        if not COMPILE:
            raise android_build.BuildInfraError('MOBILE_COMPILE must be 1 for /work')
        android_build.check_environment()
        result=await mobile_agent({'task':task,'context':json.dumps(context,ensure_ascii=False)})
        # Replace only after generation/build succeeded; QA must not see stale files.
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
        for name, text in result['files'].items():
            p=contained(folder,name);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text, encoding='utf-8')
        return {'status':'ok','tokens':result['tokens'],
                'build':{'passed':True,'tasks':['assembleDebug','testDebugUnitTest']}}
    import teamlead
    return await teamlead.run(task,context)

@app.post('/work')
async def work(data:dict):
    run_id=str(UUID(data['run_id']))
    folder=ROOT/run_id/ROLE.lower()
    contract=data['contract']
    contract_file=ROOT/run_id/'contracts/openapi.json'
    save(contract_file,contract['openapi'])
    context={'requirements':data['context'],'contract':contract,
             'contract_path':str(contract_file.relative_to(Path.cwd())),
             'output_dir':str(folder.relative_to(Path.cwd())),
             'frontend_dir':str(folder.relative_to(Path.cwd())),
             'backend_url':'http://backend:8080','max_files':30}
    task=data['task']+'\nСоблюдай переданный контракт точно. Не добавляй внешние API вне контракта.'
    reports=[]
    async with lock:
        for attempt in range(1,int(os.getenv('MAX_REPAIR_ATTEMPTS','3'))+1):
            try:
                if context.get('mode') != 'fix':
                    shutil.rmtree(folder,ignore_errors=True)
                    folder.mkdir(parents=True)
                generated=await generate(task,context,folder)
                if generated.get('status') in ('failed','partial'):
                    # own design/build failed: the tester would only rebuild the same errors
                    steps=generated.get('steps',{})
                    if 'модель недоступна' in json.dumps(steps,ensure_ascii=False):
                        # infrastructure, not code: retrying only burns the time budget
                        return {'role':ROLE,'status':'failed','stage':'llm','attempts':attempt,
                                'error':'Модель (LLM) недоступна — проверьте туннель/LLM_URL',
                                'reports':reports,'tokens':spent(reports)}
                    result={'status':'rejected','stage':'generation','error':'Агент не собрал проект: '+json.dumps(
                        {k:steps[k] for k in ('design','build_errors','build') if k in steps},ensure_ascii=False)[:6000]}
                else:
                    # direct agent -> tester call; orchestrator only receives final outcome
                    result=await request(os.environ['TESTER_URL']+'/validate',{
                        'run_id':run_id,'role':ROLE,'source':str(folder),
                        'contract':contract,'requirements':data['context'],
                        'attempt':attempt,'generation':generated})
                reports.append({'attempt':attempt,'generation':generated,'test':result})
                save(folder.parent/(ROLE.lower()+'-attempts.json'),reports)
                if result['status']=='passed' or (result['status']=='skipped' and result.get('skip_reason')=='no_kvm'):
                    return {'role':ROLE,'status':result['status'],'zip_path':result['zip_path'],
                            'skip_reason':result.get('skip_reason'),'message':result.get('message'),
                            'build':generated.get('build'),
                            'contract_hash':digest(contract),'attempts':attempt,'tokens':spent(reports)}
                feedback=result.get('feedback',{'errors':[{'message':result.get('error','QA failed')}]})
                task+='\nОбратная связь независимого тестера:\n'+json.dumps(feedback,ensure_ascii=False)
                if all(e.get('file') for e in feedback.get('errors',[])):
                    context.update(mode='fix',feedback=feedback)
                else:
                    context.pop('mode',None)
                    context.pop('feedback',None)
                if ROLE=='MOBILE':
                    task+='\nИсправь по отчёту тестера:\n'+json.dumps(feedback,ensure_ascii=False)
            except (android_build.BuildInfraError, httpx.HTTPError) as e:
                stage = 'build_infrastructure' if isinstance(e, android_build.BuildInfraError) else 'service_http'
                return {'role':ROLE,'status':'failed','stage':stage,'error':str(e),
                        'attempts':attempt,'reports':reports,'tokens':spent(reports)}
            except GenerationError as e:
                # Internal generation/build retries already exhausted.
                return {'role':ROLE,'status':'failed','stage':e.step,'error':e.to_dict(),
                        'attempts':attempt,'reports':reports,'tokens':spent(reports)}
            except (ValueError,RuntimeError,OSError) as e:
                task+='\nПредыдущая генерация отклонена: '+str(e)
                if attempt == int(os.getenv('MAX_REPAIR_ATTEMPTS','3')):
                    return {'role':ROLE,'status':'failed','error':str(e),'attempts':attempt,
                            'reports':reports,'tokens':spent(reports)}
                context.pop('mode',None)
                context.pop('feedback',None)
    return {'role':ROLE,'status':'failed','error':'Repair attempts exhausted','reports':reports,
            'attempts':len(reports),'tokens':spent(reports)}

def spent(reports):
    # generation + tester test design over all attempts, failed ones included
    return sum(x['generation'].get('tokens',0)+x['test'].get('tokens',0) for x in reports)
