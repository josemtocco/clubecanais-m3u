# ClubeCanais → M3U para SS IPTV

Gerador automático de playlist M3U a partir das páginas públicas do ClubeCanais.

## Correção desta versão

A versão anterior descobria muitos canais, mas descartava a maioria antes de montar a playlist porque o stream podia estar:

- criado somente depois que o player JavaScript era iniciado;
- em `video`, `source`, `iframe` ou `performance` do navegador;
- dentro de JSON/JavaScript;
- escapado como `https:\/\/...`;
- temporariamente bloqueado para o GitHub Actions por `401/403/429`, embora o endereço pudesse continuar utilizável no SS IPTV.

Esta versão separa descoberta, extração e validação. O navegador também captura requisições de rede e URLs de recursos carregados pelo player.

## Saídas na raiz

```text
clubecanais.m3u
canais.json
cxtv-discovery.json
```

A M3U usa `tvg-name`, `tvg-logo` e `group-title`, mantendo a categoria encontrada no ClubeCanais.

## Atualização

O GitHub Actions executa a cada 6 horas e também pode ser executado manualmente em **Actions**.

A playlist existente não é substituída se a coleta resultar em uma quantidade muito baixa de streams utilizáveis.

## Diagnóstico

`canais.json` e `cxtv-discovery.json` registram:

- total de páginas descobertas;
- canal e ID;
- categoria;
- URL do stream;
- status HTTP;
- resultado da validação.

`unknown_blocked` significa que o servidor respondeu com `401`, `403` ou `429`. Esse caso não é tratado automaticamente como canal morto, porque alguns servidores exigem cabeçalhos/contexto específicos.

## GitHub

A URL da playlist publicada será:

`https://raw.githubusercontent.com/SEU_USUARIO/SEU_REPOSITORIO/main/clubecanais.m3u`

O projeto não tenta contornar login, CAPTCHA, paywall ou mecanismos de proteção. Ele trabalha com os dados e streams que a página pública disponibiliza ao navegador.
