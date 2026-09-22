# Wake Steve (always listen)

- wake_word_enabled=true, wake_word=steve
- openWakeWord if data/wakewords/steve.onnx exists; else WhisperPhraseWakeDetector (tiny)
- AlwaysListenService background thread; ack TTS then VoiceService turn; resume
