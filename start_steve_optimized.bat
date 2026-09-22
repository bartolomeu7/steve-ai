@echo off
REM Opcional: inicia o Ollama com otimizacoes de latencia antes do Steve.
REM Nao e chamado automaticamente por nada no codigo — e so um atalho manual.
REM Ver docs/ARCHITECTURE.md ("Otimizacoes de latencia do Ollama") para o que cada
REM variavel faz e por que nao dá pra aplica-las via codigo (sao flags do servidor,
REM nao do request).
set OLLAMA_KEEP_ALIVE=24h
set OLLAMA_FLASH_ATTENTION=1
start "" ollama serve
timeout /t 3 /nobreak >nul
python main.py
