---
keywords: zone, zones, polygon, geofence, geofencing, distance, geo, area, inside, boundary, boundaries
dependencies:
---
Геометрия без библиотек: расстояние по сфере (haversine), точка в полигоне (ray casting) и расстояние до отрезка —
для зон, заданных линией (участок улицы), «внутри зоны» = ближе N метров к линии.
```kotlin
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.sqrt

data class LatLng(val lat: Double, val lng: Double)

fun distanceMeters(a: LatLng, b: LatLng): Double {
    val r = 6_371_000.0
    val dLat = Math.toRadians(b.lat - a.lat)
    val dLng = Math.toRadians(b.lng - a.lng)
    val h = sin(dLat / 2) * sin(dLat / 2) +
        cos(Math.toRadians(a.lat)) * cos(Math.toRadians(b.lat)) * sin(dLng / 2) * sin(dLng / 2)
    return 2 * r * atan2(sqrt(h), sqrt(1 - h))
}

// Полигон — вершины по порядку, без повтора первой
fun isInside(point: LatLng, polygon: List<LatLng>): Boolean {
    var inside = false
    var j = polygon.lastIndex
    for (i in polygon.indices) {
        val pi = polygon[i]
        val pj = polygon[j]
        if ((pi.lat > point.lat) != (pj.lat > point.lat) &&
            point.lng < (pj.lng - pi.lng) * (point.lat - pi.lat) / (pj.lat - pi.lat) + pi.lng
        ) inside = !inside
        j = i
    }
    return inside
}

// Плоское приближение: на отрезках в сотни метров погрешность мала
fun distanceToSegmentMeters(p: LatLng, a: LatLng, b: LatLng): Double {
    val k = cos(Math.toRadians(p.lat))
    val ax = (a.lng - p.lng) * k
    val ay = a.lat - p.lat
    val dx = (b.lng - a.lng) * k
    val dy = b.lat - a.lat
    val len2 = dx * dx + dy * dy
    val t = if (len2 == 0.0) 0.0 else (-(ax * dx + ay * dy) / len2).coerceIn(0.0, 1.0)
    val cx = ax + t * dx
    val cy = ay + t * dy
    return sqrt(cx * cx + cy * cy) * 111_320.0 // метров в градусе широты
}
```
