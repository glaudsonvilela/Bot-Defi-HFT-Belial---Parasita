# Belial A1 — observação independente / 90 minutos

**Entrega:** implementação inicial da arquitetura A1, derivada do T3 90M FIX4. Não é mais um FIX que libera extensões. Execução exclusivamente PAPER/shadow, com dados públicos. Não há transferência spot, chave privada ou envio de ordem real.

## Executar

Salve `BELIAL_A1_90M_EXECUTAR.sh` em `/home/hobit/Área de trabalho/Altside` e execute como usuário normal, sem sudo:

```bash
bash "$HOME/Área de trabalho/Altside/BELIAL_A1_90M_EXECUTAR.sh"
```

Não execute novamente o FIX4 para iniciar A1. Não é necessário extrair o ZIP. O executor verifica integridade e testes Python, instala uma release separada e inicia o supervisor. O painel abre antes da qualificação spot e da compilação dos leitores. A compilação é real, offline, com o Cargo/cache já existentes no computador; não se instala driver, compilador ou biblioteca silenciosamente.

Verificação sem instalação ou conexão a mercado:

```bash
bash "$HOME/Área de trabalho/Altside/BELIAL_A1_90M_EXECUTAR.sh" --verificar-pacote
```

## Comportamento esperado

1. **Painel independente:** estado e saúde de cada componente, inclusive durante a preparação. Fechar a aba não encerra a coleta. Se o processo da interface terminar, o supervisor pode reiniciá-lo, até três vezes, sem reiniciar a contabilidade.
2. **Livros perp por venue:** Hyperliquid e Lighter são descobertas e executadas independentemente de aprovação dos mints Solana. Falha em uma não invalida a outra. Binário nativo só é utilizado após testes e build reais daquele componente.
3. **Spot por capacidade:** identidade, mints, extensões, cofres e leitor permanecem validados. Falha de PUMP não encerra os outros ativos. TransferHook continua bloqueado para interpretação/decisão; quando houver identidade de pool válida, seu log público pode ser arquivado em modo bruto.
4. **Estratégia e N2:** os núcleos e parâmetros herdados são preservados. Somente fontes qualificadas e ativas alimentam a estratégia e o agregado da N2. Não há substituição de sinais de wallet por uma estratégia apenas de preço.
5. **Sessão congelada:** 90 minutos, com lista de capacidades congelada no início. Dados qualificados depois não entram retroativamente. Preparação, build e qualificação ficam fora dos 90 minutos. Finalização e compactação podem levar tempo adicional.

As moedas solicitadas continuam: FARTCOIN, BOME, WIF, POPCAT, RAY, JUP, BONK, PENGU, PUMP, USELESS, GRASS e PYTH. Não há substituição automática.

O painel diferencia **12 ativos solicitados**, leitores qualificados no início, fontes de sinal ativas, livros utilizáveis e pré-requisitos de PAPER disponíveis. Pré-requisitos disponíveis não são um sinal de entrada nem promessa de lucro. As duas venues podem gerar dois livros para o mesmo ativo; a contagem de livros não é a contagem de moedas.

Uma sessão com cobertura parcial não é apresentada como MP12 integral. Sem nenhuma fonte utilizável (nem leitor qualificado, nem livro válido, nem mensagens brutas observadas), a captura não inicia: o supervisor encerra com diagnóstico, em vez de contar 90 minutos vazios.

## Preservação

Base: `/mnt/sherlock-ssd/Bot Belial/Belial Negro/Raydium Hunter`.

- Release nova: `releases/belial-a1-90m-v1`.
- Cache de build novo: `target-belial-a1`.
- Runtime: `runtime/belial-a1`.
- Evidência nova: `evidence/m2-a1-<UTC>`.
- Os 81 arquivos em `legacy/` têm hashes preservados em `LEGACY_SHA256.json`.
- O motor operacional fica em `engine/`, com alterações de escopo e ciclo de vida. `Cargo.toml`, `Cargo.lock`, `m2_core.rs`, `raydium_protocols.rs` e `token_programs.rs` permanecem iguais à base.
- Regras, políticas, configuração N2/OpenCL e configuração econômica são reutilizadas; não se sobrescrevem pesos antigos. Pesos existentes só são copiados para a nova sessão quando os hashes e a configuração coincidem.
- Instalação anterior não é apagada; alteração local em uma release A1 existente causa recusa, não overwrite.

O executor recusa outro coletor Belial ativo e outra instância A1. Não mata processos antigos e não modifica serviços systemd. A1 inicia uma sessão nova; não retoma automaticamente posições após queda de energia.

## Interpretação do resultado

**PAPER perp nesta versão é a simulação dos episódios shadow herdados**, não o PnL executável da estratégia principal. A estratégia principal de referência continua separada, com seus papéis e regras originais. As carteiras das políticas e das venues são alternativas independentes, não capital a somar.

Taxas, funding, hipótese de atraso e contabilidade herdados estão em `legacy/perp/CONFIG_PAPER.json`. Spread/impacto entram nos VWAPs; não há desconto duplicado. Sem livro válido/profundidade/financiamento conhecido não se fabrica preenchimento ou funding zero. Falha spot bloqueia novas entradas dependentes, mas não apaga uma posição existente nem impede seu gerenciamento pelo próprio livro perp.

O gate inicial é de disponibilidade, qualidade e custo; não é uma previsão validada de lucro líquido da N2. N2 permanece consultiva e sem feedback para o motor de sinais. Token/SOL e contrato perp não são tratados como retornos equivalentes.

## Resultados e diagnóstico

Ao terminar ou falhar depois de criar a sessão, o supervisor tenta produzir:

```text
/home/hobit/Área de trabalho/Altside/BELIAL_A1_90M_RESULTADO_<UTC>.zip
```

O terminal imprime `RESULT_ZIP=` e `FINAL_DASHBOARD=`. O HTML final está na pasta de evidência e dentro do ZIP. Consulte `SUMMARY.json`, `REPLAY_CHECK.json`, `PLATFORM.json`, `COHORT.json`, `build/`, `spot_prep/` e os logs por componente. O arquivo ZIP contém hashes dos bytes exportados, depois da redação de URLs privadas; os originais locais são preservados.

Mantenha SSD e rede disponíveis, terminal aberto e computador sem suspensão. `Ctrl+C` solicita encerramento e relatório. Falha de espaço/gravação pode impedir o relatório completo; integridade desconhecida não é sucesso.

## Validação e limite fundamental

Foram executados **500 testes Python**, testes de subprocessos locais com falhas simuladas, análise sintática de 79 arquivos Python e teste do painel no Chromium. Consulte `VALIDACAO_LOCAL.md`.

**Não houve compilação Rust neste ambiente, teste na GPU NVIDIA, feed público real ou captura de 90 minutos.** Os testes nativos reais são gates locais antes de habilitar os respectivos binários. A1 não constitui certificação das 12 fontes atuais nem comprovação de rentabilidade. Serviços independentes podem continuar em observação mesmo quando um build/leitor estiver indisponível; isso será mostrado explicitamente.
