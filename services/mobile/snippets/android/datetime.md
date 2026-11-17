---
keywords: time, date, datetime, session, sessions, timer, duration, expire, expires, remaining, countdown, iso, schedule
dependencies:
---
Даты и время — `java.time` (доступен с minSdk 26). ISO 8601 из API — только `Instant.parse`, без своих форматтеров.
«Предупредить за N минут до окончания» — сравнивать ОСТАВШЕЕСЯ время, а не прошедшее с начала.
```kotlin
import java.time.Duration
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

fun parseInstant(iso: String): Instant = Instant.parse(iso)

// Сколько осталось до конца; отрицательное — время вышло
fun remaining(endsAt: Instant, now: Instant = Instant.now()): Duration = Duration.between(now, endsAt)

// true, когда до окончания осталось от 0 до warnBefore включительно
fun isWarningDue(endsAt: Instant, warnBefore: Duration, now: Instant = Instant.now()): Boolean {
    val left = remaining(endsAt, now)
    return !left.isNegative && left <= warnBefore
}

private val timeFormat = DateTimeFormatter.ofPattern("HH:mm").withZone(ZoneId.systemDefault())

fun formatTime(instant: Instant): String = timeFormat.format(instant)
```
