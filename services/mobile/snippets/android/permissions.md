---
keywords: permission, permissions, location, notification, notifications, activity, gps
dependencies: androidx.activity:activity-compose, androidx.core:core-ktx
---
Разрешения в рантайме из Compose: `rememberLauncherForActivityResult` + `RequestMultiplePermissions`;
POST_NOTIFICATIONS нужен только с Android 13 (TIRAMISU).
```kotlin
import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.core.content.ContextCompat

fun requiredPermissions(): Array<String> = buildList {
    add(Manifest.permission.ACCESS_FINE_LOCATION)
    add(Manifest.permission.ACCESS_COARSE_LOCATION)
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) add(Manifest.permission.POST_NOTIFICATIONS)
}.toTypedArray()

fun Context.hasPermission(permission: String): Boolean =
    ContextCompat.checkSelfPermission(this, permission) == PackageManager.PERMISSION_GRANTED

@Composable
fun RequestPermissions(onResult: (Boolean) -> Unit) {
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { granted ->
        onResult(granted.values.all { it })
    }
    LaunchedEffect(Unit) { launcher.launch(requiredPermissions()) }
}
```
