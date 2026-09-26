# Bot Belial — Estado atual

## Continuidade

Este repositório deve ser tratado como continuação direta do projeto **Belial 0.0.4**.

Raiz operacional local:

```text
/mnt/sherlock-ssd/Bot Belial/Belial Negro/Raydium Hunter
```

## Segurança operacional

- PAPER / SHADOW somente.
- Nenhuma ordem financeira real nesta fase.
- Não relaxar contratos/testes congelados apenas para fazer uma instalação passar.
- Preservar backups, hashes, instaladores e evidências locais.

## Linha atual

A linha de trabalho atual é **BELIAL A4 90M**.

O último FIX3 apresentado encerrou com:

```text
Ran 163 tests

FAILED (failures=1)

BELIAL_A4_INSTALL_FAILED=OFFLINE_SUITE_FAILED:a4
```

Falha congelada:

```text
test_a4_native_delta_is_only_main_rs
```

Delta observado:

```text
['src/main.rs', 'src/raydium_protocols.rs']
```

Contrato esperado:

```text
['src/main.rs']
```

Portanto o FIX3 não deve ser considerado uma instalação A4 aceita.

## Próximo fechamento

1. Fechar **A4 FIX4** sem enfraquecer o contrato congelado.
2. Garantir suite A4 integralmente verde.
3. Só depois abrir a próxima fase.

## Direção já aprovada para a fase seguinte

- reduzir o universo para **16 pools principais**;
- priorizar qualidade dos feeds em vez de quantidade;
- corrigir travamentos/flicker/freshness do dashboard;
- preservar a organização F1 Mercado / F2 Posições / F3 Agente / F4 Fontes;
- acelerar captura, estado de mercado e inferência;
- impedir desaparecimento silencioso de preço;
- estudar horizontes maiores para caudas observadas;
- sensores externos entram primeiro como SHADOW, não como gatilho real.

