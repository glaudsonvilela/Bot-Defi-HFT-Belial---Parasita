# Contrato N2 A3 — evidência antes de atribuição de melhoria

## Estados independentes
GPU_INITIALIZING; GPU_READY_WAITING_COHORT; WAITING_INPUT_CHANNELS; ACTIVE_NO_ELIGIBLE_FORECAST_YET; N2_ACTIVE_SHADOW; FAILED_N2_OTHER_SERVICES_CONTINUE; SESSION_FINISHED/INTERRUPTED. A saúde do processo e a idade do heartbeat prevalecem sobre um status antigo que permaneça no disco.

O supervisor cria canais vazios antes de CAPTURING. Um canal vazio não é observação. O leitor também tolera criação tardia sem `FileNotFoundError`. Linhas parciais aguardam newline. Truncamento, rotação, remoção de arquivo já aberto, symlink, JSON inválido e valores não finitos não viram dados legítimos silenciosamente.

## Causalidade
Features são congeladas antes da inferência. A publicação é carimbada depois da inferência e persistida antes da admissão aos labels. As janelas de avaliação começam a partir dessa publicação. Evento antigo, futuro, retroativo ou inelegível não produz nova previsão. Evento repetido não duplica previsão. O final da sessão drena observações pendentes sem emitir previsões após o prazo.

Os inputs continuam sendo informação spot TOKEN/WSOL e caminhos observados/amostrados. Não é uma distribuição de PnL líquido do contrato perp. As 32 cabeças foram preservadas; faltas de cobertura e janelas incompletas continuam censuradas. Nenhuma previsão controla diretamente entradas, gerenciamento de saída ou aprovação de custos.

## Auditoria de atividade
`n2/prospective/predictions.jsonl`: publicação, features, referência, probabilidades, baselines e tempo. `labels.jsonl`: resultado observado, disponibilidade, cobertura/censura e update. `AGENT_VIEW.json`: visão limitada para a tela. `status.json`: heartbeat, device, leitura dos canais e contadores. `AGENT_VALIDATION.json`: worker completo, previsões registradas, labels válidos, updates e validade dos horizontes longos.

`prospective_pipeline_demonstrated` exige worker completo e contagens positivas em previsão/label/update. Não equivale a `trading_advantage_demonstrated`, que permanece false. Sem oportunidade elegível, zero previsões é explicitamente diferente de falha do processo; o teste não inventa uma oportunidade para passar.

## Avaliação e memória
Brier e baseline são calculados sobre avaliações prospectivas não sobrepostas dentro de símbolo/cabeça, preservando os registros subjacentes. Cabeças e ativos continuam correlacionados; o total de labels não é quantidade de trades independentes. Uma diferença favorável de Brier nesta amostra não comprova rentabilidade.

A geração persistida inclui hashes dos arquivos que definem features, labels, disponibilidade, observador e universo. Apenas gerações completas A3 anteriores ao início da próxima sessão podem ser carregadas. Pesos e contadores de treinamento podem continuar; placar de avaliação e bloqueios de sobreposição reiniciam para medir a nova sessão. Pesos A1/A2 não são carregados automaticamente. Checkpoints locais da sessão incompleta ficam como evidência, não como geração aprovada.

Esta alteração estabelece funcionamento verificável do agente; não afirma que ele encontrou novos padrões rentáveis. Encontrar mais padrões exige avaliação fora da amostra e controle de tentativas, sem ajustar repetidamente contra o mesmo resultado.
