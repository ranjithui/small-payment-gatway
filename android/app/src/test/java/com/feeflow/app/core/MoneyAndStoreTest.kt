package com.feeflow.app.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.io.File
import java.nio.file.Files

class MoneyAndStoreTest {
    @Test
    fun formatsIndianGrouping() {
        assertEquals("Rs. 0", Money.format(0))
        assertEquals("Rs. 2,500", Money.format(250000))
        assertEquals("Rs. 2,500.50", Money.format(250050))
        assertEquals("Rs. 1,25,000", Money.format(12500000))
        assertEquals("Rs. 10,00,000.05", Money.format(100000005))
    }

    @Test
    fun parsesAmounts() {
        assertEquals(250000L, Money.parseToPaise("2500"))
        assertEquals(250050L, Money.parseToPaise("2,500.5"))
        assertEquals(250050L, Money.parseToPaise("Rs. 2500.50"))
        assertNull(Money.parseToPaise("0"))
        assertNull(Money.parseToPaise("-5"))
        assertNull(Money.parseToPaise("12.345"))
        assertNull(Money.parseToPaise("abc"))
        assertEquals("2500", Money.toInput(250000))
        assertEquals("2500.05", Money.toInput(250005))
    }

    @Test
    fun parsesServerTimestamps() {
        assertEquals(1794388931123L, Times.parse("2026-11-11T09:22:11.123000Z")!!.toEpochMilli())
        assertEquals(1794388931000L, Times.parse("2026-11-11T09:22:11Z")!!.toEpochMilli())
        assertNull(Times.parse("not a date"))
        assertNull(Times.parse(null))
    }

    @Test
    fun fileStoreRoundTripsAndRecoversFromCorruption() {
        val dir = Files.createTempDirectory("ffstore").toFile()
        val file = File(dir, "state.json")
        val store = FileStateStore(file)
        assertEquals(EngineState(), store.load())

        val s1 = EngineState(sentSms = mapOf("a" to SentSms("SENT", 1)), lastSyncAtMs = 5)
        store.save(s1)
        assertEquals(s1, FileStateStore(file).load())

        val s2 = s1.copy(sentSms = s1.sentSms + ("b" to SentSms("FAILED", 2, "x")))
        store.save(s2)
        file.writeText("{corrupt")
        // Falls back to the previous good copy instead of forgetting what was already sent
        assertEquals(s1, FileStateStore(file).load())
        dir.deleteRecursively()
    }
}
