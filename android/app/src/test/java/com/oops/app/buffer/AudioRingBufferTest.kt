package com.oops.app.buffer
import org.junit.Assert.*
import org.junit.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder
class AudioRingBufferTest {
    private fun pcm(vararg samples: Int) = ByteBuffer.allocate(samples.size*2).order(ByteOrder.LITTLE_ENDIAN).apply { samples.forEach { putShort(it.toShort()) } }.array()
    @Test fun wrapReadsInTimelineOrder() { val b=AudioRingBuffer(4);b.append(pcm(1,2,3));b.append(pcm(4,5,6));assertArrayEquals(pcm(3,4,5,6),b.read(2,6));assertEquals(6,b.endSample) }
    @Test fun overwrittenAndFutureRangesAreUnavailable() { val b=AudioRingBuffer(3);b.append(pcm(1,2,3,4));assertNull(b.read(0,2));assertNull(b.read(2,5));assertNull(b.read(2,2)) }
    @Test fun availableReadClipsBothEnds() { val b=AudioRingBuffer(3);b.append(pcm(1,2,3,4));val r=b.readAvailable(0,8)!!;assertEquals(1,r.first);assertArrayEquals(pcm(2,3,4),r.second) }
    @Test fun silenceAdvancesClockAndWraps() { val b=AudioRingBuffer(4);b.append(pcm(7,8));b.appendSilence(3);assertEquals(5,b.endSample);assertArrayEquals(pcm(8,0,0,0),b.read(1,5));b.appendSilence(10);assertEquals(15,b.endSample);assertArrayEquals(pcm(0,0,0,0),b.read(11,15)) }
    @Test fun oversizedAppendRetainsNewestSamples() { val b=AudioRingBuffer(3);b.append(pcm(1,2,3,4,5,6,7));assertEquals(4,b.startSample);assertArrayEquals(pcm(5,6,7),b.read(4,7)) }
    @Test fun resetMakesPriorSamplesUnavailable() { val b=AudioRingBuffer(3);b.append(pcm(1,2));b.reset();assertEquals(0,b.endSample);assertNull(b.read(0,1)) }
    @Test fun finalAsrEndTwoMillisecondsPastAudioStillReturnsRecordedSpeech() {
        val b = AudioRingBuffer(16_000)
        val speech = ByteArray(32_000) { (it % 127).toByte() }
        b.append(speech)
        assertNull(b.read(0, 16_032))
        assertArrayEquals(speech, b.readWithEndTolerance(0, 16_032, 1_600))
        assertNull(b.readWithEndTolerance(0, 17_601, 1_600))
    }
    @Test fun endToleranceDoesNotReuseOverwrittenOrEmptyAudio() {
        val b = AudioRingBuffer(4)
        b.append(pcm(1,2,3,4,5,6))
        assertNull(b.readWithEndTolerance(0, 7, 2))
        assertNull(b.readWithEndTolerance(6, 7, 2))
        assertArrayEquals(pcm(5,6), b.readWithEndTolerance(4, 7, 2))
    }
}
