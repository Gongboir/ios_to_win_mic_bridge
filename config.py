SAMPLE_RATE    = 48000
CHANNELS       = 1
CHUNK_SAMPLES  = 2048
DTYPE          = 'float32'
HTTP_PORT      = 8080
RING_BUF_SIZE  = CHUNK_SAMPLES * 8  # 8 chunks of headroom

VBCABLE_NAME_HINTS = ('cable input', 'vb-audio', 'vb-cable')
