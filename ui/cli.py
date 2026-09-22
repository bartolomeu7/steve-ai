"""Simple text chat loop, with an optional voice turn on top of the same Orchestrator.
V1 keeps the UI minimal by design; a richer UI comes later."""
from __future__ import annotations

from core.orchestrator import Orchestrator
from voice.service import VoiceService
from voice.tts import TextToSpeech

EXIT_COMMANDS = {"sair", "exit", "quit"}


def run_chat_loop(
    orchestrator: Orchestrator,
    tts: TextToSpeech | None = None,
    voice_output_enabled: bool = False,
    voice_service: VoiceService | None = None,
    push_to_talk_key: str = "v",
    voice_streaming_enabled: bool = False,
) -> None:
    if voice_service is not None:
        print(f"Digite sua mensagem, '{push_to_talk_key}' para falar com o Steve, ou 'sair' para encerrar.\n")
    else:
        print("Digite 'sair' para encerrar.\n")

    while True:
        try:
            user_text = input("Você: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSteve: Até logo.")
            break

        if not user_text:
            continue
        if user_text.lower() in EXIT_COMMANDS:
            print("Steve: Até logo.")
            break

        if voice_service is not None and user_text.lower() == push_to_talk_key.lower():
            _run_voice_turn(voice_service, streaming=voice_streaming_enabled)
            continue

        reply = orchestrator.handle_message(user_text)
        print(f"Steve: {reply}")

        if voice_output_enabled and tts is not None and tts.is_available():
            tts.speak(reply)


def _run_voice_turn(voice_service: VoiceService, streaming: bool = False) -> None:
    if streaming:
        result = voice_service.listen_and_respond_streaming(on_state=print)
    else:
        result = voice_service.listen_and_respond(on_state=print)
    if not result.success:
        print(f"Steve: {result.error}")
        return
    print(f"Você (voz): {result.transcript}")
    print(f"Steve: {result.reply}")
