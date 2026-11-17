---
keywords: activity, navigation, navhost, theme
dependencies: androidx.activity:activity-compose, androidx.compose.material3:material3, androidx.navigation:navigation-compose
---
Точка входа: `ComponentActivity` + `setContent`, тема — `MaterialTheme` из Material 3 (своих ресурсов темы нет),
навигация — `NavHost` со строковыми маршрутами.
```kotlin
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Surface { AppNavHost() }
            }
        }
    }
}

@Composable
fun AppNavHost() {
    val nav = rememberNavController()
    NavHost(navController = nav, startDestination = "main") {
        composable("main") {
            Button(onClick = { nav.navigate("history") }) { Text("История") }
        }
        composable("history") { Text("История") }
    }
}
```
