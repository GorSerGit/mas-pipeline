---
keywords: model, models, dto, serializable, json, request, response, entity, entities
dependencies: org.jetbrains.kotlinx:kotlinx-serialization-json
---
Модели API: `@Serializable`, имена полей контракта (snake_case) — через `@SerialName`, необязательные поля —
nullable со значением по умолчанию, даты — строкой ISO 8601 (разбор через `java.time.Instant.parse`).
```kotlin
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
enum class SessionStatus {
    @SerialName("ACTIVE") ACTIVE,
    @SerialName("COMPLETED") COMPLETED,
}

@Serializable
data class GeoPointDto(val latitude: Double, val longitude: Double)

@Serializable
data class SessionDto(
    val id: String,
    @SerialName("zone_id") val zoneId: String,
    @SerialName("started_at") val startedAt: String,
    @SerialName("ends_at") val endsAt: String? = null,
    val status: SessionStatus,
    val location: GeoPointDto? = null,
)
```
