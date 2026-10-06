package com.oops.app.core
import org.junit.Assert.*
import org.junit.Test
import java.io.ByteArrayInputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
class WavTest {
 @Test fun headerAndPcmRoundTrip() { val data=byteArrayOf(1,0,2,0,3,0);assertArrayEquals(data,Wav.readPcm(ByteArrayInputStream(Wav.header(data.size)+data))) }
 @Test(expected=IllegalArgumentException::class) fun otherSampleRatesAreRejected() { val h=Wav.header(2);ByteBuffer.wrap(h).order(ByteOrder.LITTLE_ENDIAN).putInt(24,44100);Wav.readPcm(ByteArrayInputStream(h+byteArrayOf(0,0))) }
 @Test(expected=IllegalArgumentException::class) fun floatingPointWavIsRejected() { val h=Wav.header(2);h[20]=3;Wav.readPcm(ByteArrayInputStream(h+byteArrayOf(0,0))) }
 @Test(expected=IllegalArgumentException::class) fun truncatedDataIsRejected() { Wav.readPcm(ByteArrayInputStream(Wav.header(4)+byteArrayOf(0,0))) }
 @Test fun streamingOpenReadsOnlyHeader() { var count=0;val bytes=Wav.header(320000)+ByteArray(320000);val input=object:ByteArrayInputStream(bytes) { override fun read(b:ByteArray,off:Int,len:Int):Int { val n=super.read(b,off,len);count+=n.coerceAtLeast(0);return n } };val pcm=Wav.openPcm(input);assertEquals(44,count);val chunk=ByteArray(3200);assertEquals(3200,pcm.read(chunk));assertEquals(3244,count) }
 @Test fun oddUnknownChunksAreSkippedWithPadding() { val base=Wav.header(2);val junk="JUNK".toByteArray()+byteArrayOf(1,0,0,0,5,0);val wav=base.take(12).toByteArray()+junk+base.drop(12).toByteArray()+byteArrayOf(9,0);assertArrayEquals(byteArrayOf(9,0),Wav.readPcm(ByteArrayInputStream(wav))) }
}
