---
keywords: test, tests, unittest, junit
dependencies: junit:junit, org.jetbrains.kotlinx:kotlinx-coroutines-test
source: test
---
Unit-тесты: JUnit 4, Double — всегда с допуском, suspend-код и Flow — внутри `runTest`.
Ожидаемые значения считай заранее: 0.0001° широты ≈ 11 м, 0.0001° долготы на 60° с.ш. ≈ 5,6 м.
Проверяй критерии приёмки из контекста (например, «за 10 минут до окончания»), а не только устройство кода.
```kotlin
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class Meters {
    fun toKm(meters: Double) = meters / 1000
    fun isNear(meters: Double, limit: Double) = meters <= limit
}

class MetersTest {
    private val meters = Meters()

    @Test
    fun convertsToKm() {
        assertEquals(1.5, meters.toKm(1500.0), 1e-9)
    }

    @Test
    fun boundaryIsInclusive() {
        assertTrue(meters.isNear(30.0, 30.0))
        assertFalse(meters.isNear(30.1, 30.0))
    }

    @Test
    fun suspendCode() = runTest {
        val state = MutableStateFlow(1)
        state.value = 2
        assertEquals(2, state.first())
    }
}
```
