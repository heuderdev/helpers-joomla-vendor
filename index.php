<?php

declare (strict_types = 1);

require_once __DIR__ . '/vendor/autoload.php';

$arquivo = 'pt18_cnv2026_desempenho_s4.csv';
$tabela  = 'pt18_cnv_desempenhos4';
$conexao = 'upload';

try {
    $config = require __DIR__ . '/config.php';

    DbConnectionHelper::register($conexao, $config['UPLOAD']);


    $query = OrmTables::table($tabela, [
        'connection' => $conexao,
    ])->get();
    $arquivoDestino = ExportHelper::getBaseDirectory().'/exports/pedidos-'. date('Y-m-d-His'). '.csv';
    ExportHelper::download($arquivoDestino, $query,
        [
            'id'                      => 'ID',
            'NUM_CENTRAL'             => 'NUM_CENTRAL',
            'NUM_COOP'                => 'NUM_COOP',
            'ID_PRODUTO'              => 'ID_PRODUTO',
            'PRODUTO'                 => 'PRODUTO',
            'COMBO'                   => 'COMBO',
            'GRUPO_S'                 => 'GRUPO_S',
            'PRODUCAO'                => 'PRODUCAO',
            'RANK_PRODUCAO'           => 'RANK_PRODUCAO',
            'PONTO_PRODUCAO'          => 'PONTO_PRODUCAO',
            'DATA_BASE'               => 'DATA_BASE',
            'TOTAL_COMBO'             => 'TOTAL_COMBO',
            'TOTAL_RANKING'           => 'TOTAL_RANKING',
            'STATUS'                  => 'STATUS',
            'PONTO_PRODUTO_ACUM'      => 'PONTO_PRODUTO_ACUM',
            'DESEMPATE_PRODUCAO_ACUM' => 'DESEMPATE_PRODUCAO_ACUM',
            'DATA_ATUALIZACAO'        => 'DATA_ATUALIZACAO',
        ]
        , ['utf8_bom' => true, 'overwrite' => false]);
        
    $mapeamento = [
        'num_central'             => 'NUM_CENTRAL',
        'num_coop'                => 'NUM_COOP',
        'id_produto'              => 'ID_PRODUTO',
        'produto'                 => 'PRODUTO',
        'combo'                   => 'COMBO',
        'grupo_s'                 => 'GRUPO_S',
        'producao'                => 'PRODUCAO',
        'rank_producao'           => 'RANK_PRODUCAO',
        'ponto_producao'          => 'PONTO_PRODUCAO',
        'data_base'               => 'DATA_BASE',
        'total_combo'             => 'TOTAL_COMBO',
        'total_ranking'           => 'TOTAL_RANKING',
        'status'                  => 'STATUS',
        'ponto_produto_acum'      => 'PONTO_PRODUTO_ACUM',
        'desempate_producao_acum' => 'DESEMPATE_PRODUCAO_ACUM',
        'data_atualizacaos'       => 'DATA_ATUALIZACAO',
    ];

    $tipos = [
        'NUM_CENTRAL'             => 'int',
        'NUM_COOP'                => 'int',
        'ID_PRODUTO'              => 'int',
        'PRODUTO'                 => 'string',
        'COMBO'                   => 'string',
        'GRUPO_S'                 => 'string',
        'PRODUCAO'                => 'string',
        'RANK_PRODUCAO'           => 'int',
        'PONTO_PRODUCAO'          => 'string',
        'DATA_BASE'               => 'date',
        'TOTAL_COMBO'             => 'string',
        'TOTAL_RANKING'           => 'int',
        'STATUS'                  => 'string',
        'PONTO_PRODUTO_ACUM'      => 'string',
        'DESEMPATE_PRODUCAO_ACUM' => 'string',
        'DATA_ATUALIZACAO'        => 'date',
    ];

    $opcoes = [
        'connection'       => $conexao,
        'mode'             => 'insert',
        'mapping'          => $mapeamento,
        'required_headers' => array_keys($mapeamento),
        'types'            => $tipos,
        'convert'          => true,
        'empty_as_null'    => true,
        'chunk_size'       => 1000,
        'atomic'           => true,
        'stop_on_error'    => true,
    ];

    // Confere os cabeçalhos de origem no CSV.
    $cabecalhos = CsvHelper::headers($arquivo, null, $opcoes);

    if (($cabecalhos['success'] ?? false) !== true) {
        throw new RuntimeException(
            (string) (
                $cabecalhos['mensagem'] ?? 'Não foi possível ler os cabeçalhos do CSV.'
            )
        );
    }

    $headersEncontrados = $cabecalhos['data']['headers_normalized'];

    $headersAusentes = array_values(
        array_diff(
            array_keys($mapeamento),
            $headersEncontrados
        )
    );

    // Confere as colunas de destino na tabela.
    $db = DbConnectionHelper::get($conexao);

    $estrutura = (array) $db->getTableColumns($tabela, true);

    if ($estrutura === []) {
        throw new RuntimeException(
            'Não foi possível identificar as colunas da tabela '
            . $tabela
            . '.'
        );
    }

    $colunasDaTabela      = array_keys($estrutura);
    $mapeamentosInvalidos = [];

    foreach ($mapeamento as $origem => $destino) {
        if (! in_array($destino, $colunasDaTabela, true)) {
            $mapeamentosInvalidos[$origem] = $destino;
        }
    }

    // Mostra os problemas estruturais antes de iniciar a importação.
    if ($headersAusentes !== [] || $mapeamentosInvalidos !== []) {
        fwrite(
            STDERR,
            'Importação cancelada antes de iniciar as gravações.'
            . PHP_EOL
        );

        if ($headersAusentes !== []) {
            fwrite(
                STDERR,
                'Cabeçalhos obrigatórios ausentes no CSV: '
                . implode(', ', $headersAusentes)
                . '.'
                . PHP_EOL
            );
        }

        foreach ($mapeamentosInvalidos as $origem => $destino) {
            fwrite(
                STDERR,
                sprintf(
                    'Mapeamento inválido: CSV "%s" aponta para '
                    . 'a coluna inexistente "%s" na tabela "%s".%s',
                    $origem,
                    $destino,
                    $tabela,
                    PHP_EOL
                )
            );
        }

        exit(1);
    }

    // O helper controla a transação global por meio de atomic.
    $resultado = CsvHelper::import(
        $arquivo,
        $tabela,
        null,
        $opcoes
    );

    if (($resultado['success'] ?? false) !== true) {
        fwrite(
            STDERR,
            'ERRO: '
            . (string) (
                $resultado['mensagem'] ?? 'Não foi possível importar o arquivo.'
            )
            . PHP_EOL
        );

        foreach ($resultado['errors'] ?? [] as $erro) {
            fwrite(
                STDERR,
                sprintf(
                    'Linha %s: %s%s',
                    (string) ($erro['line'] ?? '?'),
                    (string) (
                        $erro['message'] ?? 'Erro sem descrição disponível.'
                    ),
                    PHP_EOL
                )
            );
        }

        exit(1);
    }

    $relatorio = $resultado['data'] ?? [];

    fwrite(
        STDOUT,
        'Importação concluída com sucesso.' . PHP_EOL
    );

    fwrite(
        STDOUT,
        sprintf(
            'Linhas processadas: %d | Linhas aceitas: %d%s',
            (int) ($relatorio['total_rows'] ?? 0),
            (int) ($relatorio['success_rows'] ?? 0),
            PHP_EOL
        )
    );

    exit(0);
} catch (Throwable $erro) {
    fwrite(
        STDERR,
        'ERRO: ' . $erro->getMessage() . PHP_EOL
    );

    exit(1);
}
