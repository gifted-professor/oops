package com.oops.app.audio

import org.junit.Assert.*
import org.junit.Test

class VoiceprintCaptureTest {
    @Test fun recordingIsBoundedAndStopsAtThirtySecondsEvenWithOversizedFrames() {
        val recording = VoiceprintCapture(30_000)
        assertFalse(recording.append(ByteArray(29 * 32000) { 1 }))
        assertTrue(recording.append(ByteArray(2 * 32000) { 2 }))
        assertEquals(30_000, recording.elapsedMs)
        assertTrue(recording.append(ByteArray(32000) { 3 }))
        val pcm = recording.finish()
        assertEquals(30 * 32000, pcm.size)
        assertTrue(pcm.takeLast(32000).all { it == 2.toByte() })
        assertEquals(0, recording.elapsedMs)
    }

    @Test fun finishingReturnsAnIndependentRecordingAndClearsTemporaryBuffer() {
        val recording = VoiceprintCapture(10_000)
        val frame = ByteArray(3200) { 7 }
        recording.append(frame)
        frame.fill(0)
        val pcm = recording.finish()
        assertTrue(pcm.all { it == 7.toByte() })
        assertEquals(0, recording.finish().size)
    }
}
