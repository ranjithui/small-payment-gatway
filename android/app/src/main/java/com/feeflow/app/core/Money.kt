package com.feeflow.app.core

import java.math.BigDecimal
import java.math.RoundingMode
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

object Money {
    /** 250000 -> "Rs. 2,500"; 250050 -> "Rs. 2,500.50" (Indian digit grouping). */
    fun format(paise: Long): String {
        val negative = paise < 0
        val abs = kotlin.math.abs(paise)
        val rupees = abs / 100
        val p = abs % 100
        val grouped = groupIndian(rupees.toString())
        val body = if (p == 0L) grouped else "$grouped.${p.toString().padStart(2, '0')}"
        return (if (negative) "-" else "") + "Rs. " + body
    }

    /** "2500", "2,500.5", "Rs 2500.50" -> paise. Returns null for invalid / non-positive / >2 decimals. */
    fun parseToPaise(input: String): Long? {
        val cleaned = input.trim().replace(Regex("(?i)^rs\\.?\\s*"), "").replace(",", "")
        if (cleaned.isEmpty() || !cleaned.matches(Regex("\\d+(\\.\\d{1,2})?"))) return null
        val value = BigDecimal(cleaned).multiply(BigDecimal(100)).setScale(0, RoundingMode.UNNECESSARY)
        val paise = value.toLong()
        return if (paise > 0) paise else null
    }

    /** 250000 -> "2500"; 250050 -> "2500.50" (for prefilling editable amount fields). */
    fun toInput(paise: Long): String =
        if (paise % 100 == 0L) (paise / 100).toString() else "${paise / 100}.${(paise % 100).toString().padStart(2, '0')}"

    private fun groupIndian(digits: String): String {
        if (digits.length <= 3) return digits
        val last3 = digits.takeLast(3)
        val rest = digits.dropLast(3)
        val pairs = rest.reversed().chunked(2).joinToString(",").reversed()
        return "$pairs,$last3"
    }
}

object Times {
    private val display = DateTimeFormatter.ofPattern("d MMM yyyy, h:mm a", Locale.ENGLISH)

    fun parse(iso: String?): Instant? = try {
        iso?.let { Instant.parse(it) }
    } catch (_: Exception) {
        null
    }

    fun display(iso: String?, zone: ZoneId = ZoneId.systemDefault()): String =
        parse(iso)?.atZone(zone)?.format(display) ?: "-"

    fun display(instant: Instant?, zone: ZoneId = ZoneId.systemDefault()): String =
        instant?.atZone(zone)?.format(display) ?: "-"
}
