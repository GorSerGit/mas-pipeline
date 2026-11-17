---
keywords: viewmodel, state, screen, ui, factory, presentation
dependencies: androidx.lifecycle:lifecycle-viewmodel-compose, androidx.lifecycle:lifecycle-runtime-compose, androidx.compose.material3:material3
---
ViewModel со StateFlow; ViewModel с параметрами конструктора создаётся только через фабрику `viewModelFactory`,
в Compose состояние собирается `collectAsStateWithLifecycle()`.
```kotlin
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class CounterUiState(val value: Int = 0, val error: String? = null)

class CounterRepository {
    suspend fun load(): Int = 42
}

class CounterViewModel(private val repository: CounterRepository) : ViewModel() {
    private val _state = MutableStateFlow(CounterUiState())
    val state: StateFlow<CounterUiState> = _state.asStateFlow()

    fun refresh() {
        viewModelScope.launch {
            try {
                val value = repository.load()
                _state.update { it.copy(value = value, error = null) }
            } catch (e: Exception) {
                _state.update { it.copy(error = e.message) }
            }
        }
    }

    companion object {
        fun factory(repository: CounterRepository): ViewModelProvider.Factory = viewModelFactory {
            initializer { CounterViewModel(repository) }
        }
    }
}

@Composable
fun CounterScreen(repository: CounterRepository) {
    val vm: CounterViewModel = viewModel(factory = CounterViewModel.factory(repository))
    val state by vm.state.collectAsStateWithLifecycle()
    Text("Значение: ${state.value}")
}
```
