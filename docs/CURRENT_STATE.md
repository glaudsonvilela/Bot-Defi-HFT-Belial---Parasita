# Bot Belial — Estado atual

## Linha

**BELIAL A5.1R 16Q / 9H RESILIENTE**

Base A5.1:
`4b67576f49f391b7847f18fb4b7c2ecd0f370ff8 feat(a51): clean runtime and add shadow satellites for 9h`

## Segurança

- PAPER / SHADOW;
- orders=false;
- satélites Binance/Bybit/Kraken somente contexto;
- estratégia/sizing/saídas inalterados;
- recuperação por segmentos, sem gap-fill;
- posições PAPER não atravessam gap de reboot/segmento.

## Execução

9 horas de tempo PRIMARY-READY observado.
Systemd user + linger retomam o run manager após reboot.
