package com.oops.app.asr
import org.junit.Assert.*
import org.junit.Test
import java.nio.ByteBuffer
class VolcProtocolTest {
 @Test fun requestHeadersMatchWireFormat() { assertArrayEquals(byteArrayOf(0x11,0x10,0x11,0),VolcProtocol.fullClientRequest("{}").take(4).toByteArray());assertArrayEquals(byteArrayOf(0x11,0x20,0,0),VolcProtocol.audio(byteArrayOf(1,2),false).take(4).toByteArray());assertArrayEquals(byteArrayOf(0x11,0x22,0,0),VolcProtocol.audio(byteArrayOf(),true).take(4).toByteArray()) }
 @Test fun payloadLengthIsBigEndian() { val f=VolcProtocol.audio(ByteArray(258),false);assertArrayEquals(byteArrayOf(0,0,1,2),f.sliceArray(4..7));assertEquals(266,f.size) }
 @Test fun sequenceAndGzipDecode() { val f=VolcProtocol.encode(9,3,1,1,VolcProtocol.gzip("你好".toByteArray()),-7);val r=VolcProtocol.decode(f) as VolcProtocol.ServerMessage.Response;assertEquals(-7,r.sequence);assertTrue(r.isLast);assertEquals("你好",r.payload) }
 @Test fun errorFrameDecodesCodeAndMessage() { val text="bad token".toByteArray();val f=byteArrayOf(0x11,0xf0.toByte(),0x10,0)+ByteBuffer.allocate(8).putInt(45000001).putInt(text.size).array()+text;assertEquals(VolcProtocol.ServerMessage.Error(45000001,"bad token"),VolcProtocol.decode(f)) }
 @Test(expected=IllegalArgumentException::class) fun shortFrameIsRejected() { VolcProtocol.decode(byteArrayOf(0x11)) }
}
