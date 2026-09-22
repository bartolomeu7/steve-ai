# Design: Player interno YouTube (WebView2)

Aprovado: janela filha WebView2 (pywebview/Edge), 1o video autoplay maximizado.

- play_music resolve query -> EventBus media.play
- SteveApp abre media_player_host.py (processo filho) com embed autoplay
- Sem webbrowser.open no caminho GUI
