package com.algotrading.app

import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Query

data class MarketStatus(
    val status: String,
    val message: String
)

data class QuoteData(
    val symbol: String,
    val exchange: String,
    val bidPrice: Double,
    val askPrice: Double,
    val ltp: Double,
    val spread: Double
)

data class OrderRequest(
    val symbol: String,
    val quantity: Int,
    val transactionType: String // "BUY" or "SELL"
)

data class OrderResponse(
    val orderId: String,
    val status: String,
    val message: String
)

interface ApiInterface {
    @GET("/")
    suspend fun getRootStatus(): MarketStatus

    @GET("/api/v1/quote")
    suspend fun getBidAskQuote(@Query("symbol") symbol: String): QuoteData

    @POST("/api/v1/order")
    suspend fun placeOrder(@Body request: OrderRequest): OrderResponse
}

object ApiService {
    private const val BASE_URL = "http://10.0.2.2:8000/"

    val retrofitService: ApiInterface by lazy {
        Retrofit.Builder()
            .addConverterFactory(GsonConverterFactory.create())
            .baseUrl(BASE_URL)
            .build()
            .create(ApiInterface::class.java)
    }
}
