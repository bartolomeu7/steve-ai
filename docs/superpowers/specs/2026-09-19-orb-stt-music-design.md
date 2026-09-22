# Design: Orb liquido + STT + play_music (2026-09-19)

## Orb (Approach A)
- Glow respirante ciano cyber, anti-alias via supersample 3x + Lanczos
- Halo difuso; aneis plasticos reduzidos no IDLE
- Chip IDLE: bullet + detalhe Pronto para conversar (sem Pronto duplicado)

## STT
- stt_model_size: tiny -> base
- initial_prompt pt-BR no faster-whisper
- language permanece pt

## Musica
- Nova tool play_music (LOW): abre YouTube search com a query do usuario
- REUSE padrao open_url / webbrowser
