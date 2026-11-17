---
keywords: api, retrofit, network, http, client, rest, backend, container, service
dependencies: com.squareup.retrofit2:retrofit, com.squareup.retrofit2:converter-kotlinx-serialization, com.squareup.okhttp3:okhttp, org.jetbrains.kotlinx:kotlinx-serialization-json
---
REST-клиент: Retrofit + kotlinx.serialization. Конвертер встроен в Retrofit (`retrofit2.converter.kotlinx.serialization`),
MediaType — через `toMediaType()` с явным импортом. Пути без ведущего "/", BASE_URL заканчивается на "/".
```kotlin
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query

@Serializable
data class ItemDto(val id: String, val name: String)

@Serializable
data class RenameRequest(val name: String)

interface ItemsApi {
    @GET("api/v1/items")
    suspend fun items(@Query("city") city: String): List<ItemDto>

    @POST("api/v1/items/{id}/rename")
    suspend fun rename(@Path("id") id: String, @Body body: RenameRequest): ItemDto
}

object Network {
    private val json = Json { ignoreUnknownKeys = true }

    private val retrofit: Retrofit = Retrofit.Builder()
        .baseUrl(BuildConfig.BASE_URL)
        .client(OkHttpClient.Builder().build())
        .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
        .build()

    val itemsApi: ItemsApi = retrofit.create(ItemsApi::class.java)
}
```
