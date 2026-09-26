# Belial A5.1R — 9h resiliente

Base canônica: `4b67576f49f391b7847f18fb4b7c2ecd0f370ff8`.

- A5.1 Clean/Perf + Binance/Bybit/Kraken SHADOW preservado;
- Rust preservado byte-a-byte;
- estratégia/sizing/saídas inalterados;
- systemd user service + linger;
- retomada após reboot em novo segmento;
- gaps de rede/energia explícitos;
- tempo de gap não conta para a meta;
- >=180 s sem caminho primário reinicia o segmento;
- prefixo completo do journal PAPER é replayado para auditoria;
- posições PAPER não atravessam gap entre segmentos;
- nenhuma lacuna de mercado é inventada;
- orders=false.

A meta é 32.400.000 ms de tempo PRIMARY-READY observado.
