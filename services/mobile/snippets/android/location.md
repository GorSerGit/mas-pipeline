---
keywords: location, gps, geo, route, tracking, tracker, coordinates, position, mock, movement
dependencies: com.google.android.gms:play-services-location, org.jetbrains.kotlinx:kotlinx-coroutines-android
---
Координаты — через интерфейс-источник: настоящий GPS (FusedLocationProviderClient + `LocationRequest.Builder`)
и mock-маршрут для демонстрации взаимозаменяемы. Mock-маршрут — список точек с паузой: машина «едет».
```kotlin
import android.annotation.SuppressLint
import android.content.Context
import android.os.Looper
import com.google.android.gms.location.LocationCallback
import com.google.android.gms.location.LocationRequest
import com.google.android.gms.location.LocationResult
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.flow

data class Position(val latitude: Double, val longitude: Double)

interface LocationSource {
    fun positions(): Flow<Position>
}

class GpsLocationSource(context: Context, private val intervalMs: Long = 5_000) : LocationSource {
    private val client = LocationServices.getFusedLocationProviderClient(context)

    @SuppressLint("MissingPermission") // разрешение проверяется до подписки
    override fun positions(): Flow<Position> = callbackFlow {
        val request = LocationRequest.Builder(Priority.PRIORITY_HIGH_ACCURACY, intervalMs).build()
        val callback = object : LocationCallback() {
            override fun onLocationResult(result: LocationResult) {
                result.lastLocation?.let { trySend(Position(it.latitude, it.longitude)) }
            }
        }
        client.requestLocationUpdates(request, callback, Looper.getMainLooper())
        awaitClose { client.removeLocationUpdates(callback) }
    }
}

class MockRouteSource(private val route: List<Position>, private val stepMs: Long = 3_000) : LocationSource {
    override fun positions(): Flow<Position> = flow {
        for (point in route) {
            emit(point)
            delay(stepMs)
        }
    }
}
```
