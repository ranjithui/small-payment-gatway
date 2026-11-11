package com.feeflow.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

val Brand = Color(0xFF4F46E5)
val Ok = Color(0xFF047857)
val OkBg = Color(0xFFD1FAE5)
val Warn = Color(0xFF92400E)
val WarnBg = Color(0xFFFEF3C7)
val Bad = Color(0xFFB91C1C)
val BadBg = Color(0xFFFEE2E2)

@Composable
fun FeeFlowTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = lightColorScheme(
            primary = Brand,
            secondary = Ok,
            error = Bad,
            background = Color(0xFFF4F6FB),
            surface = Color.White,
        ),
        content = content,
    )
}

@Composable
fun StatusPill(status: String) {
    val (fg, bg) = when (status) {
        "VERIFIED", "PAID", "SENT", "SYNCED" -> Ok to OkBg
        "PAYMENT_REPORTED", "PENDING_VERIFICATION", "PAYMENT_PROCESSING", "PARTIALLY_PAID" -> Warn to WarnBg
        "REJECTED", "FAILED", "UNPAID", "EXPIRED" -> Bad to BadBg
        else -> Brand to Color(0xFFE0E7FF)
    }
    Text(
        status.replace('_', ' '),
        color = fg,
        fontSize = 11.sp,
        fontWeight = FontWeight.Bold,
        modifier = Modifier
            .background(bg, RoundedCornerShape(50))
            .padding(horizontal = 10.dp, vertical = 3.dp),
    )
}
