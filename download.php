<?php

declare(strict_types=1);

require_once __DIR__ . '/vendor/autoload.php';

$tabela = 'pt18_cnv_desempenhos4';
$conexao = 'upload';

try {
    $config = require __DIR__ . '/config.php';

    DbConnectionHelper::register($conexao, $config['UPLOAD']);
    $registros = OrmTables::table($tabela, [
        'connection' => $conexao,
    ])->get();

    $colunas = [
        'id'                     => 'ID',
        'NUM_CENTRAL'            => 'NUM_CENTRAL',
        'NUM_COOP'               => 'NUM_COOP',
        'ID_PRODUTO'             => 'ID_PRODUTO',
        'PRODUTO'                => 'PRODUTO',
        'COMBO'                  => 'COMBO',
        'GRUPO_S'                => 'GRUPO_S',
        'PRODUCAO'               => 'PRODUCAO',
        'RANK_PRODUCAO'          => 'RANK_PRODUCAO',
        'PONTO_PRODUCAO'         => 'PONTO_PRODUCAO',
        'DATA_BASE'              => 'DATA_BASE',
        'TOTAL_COMBO'            => 'TOTAL_COMBO',
        'TOTAL_RANKING'          => 'TOTAL_RANKING',
        'STATUS'                 => 'STATUS',
        'PONTO_PRODUTO_ACUM'      => 'PONTO_PRODUTO_ACUM',
        'DESEMPATE_PRODUCAO_ACUM' => 'DESEMPATE_PRODUCAO_ACUM',
        'DATA_ATUALIZACAO'        => 'DATA_ATUALIZACAO',
    ];

    // Caminho relativo ao diretório-base do ExportHelper.
    $arquivoDestino = 'exports/pedidos-'
        . date('Y-m-d-His')
        . '.csv';

    $resultado = ExportHelper::csv(
        $arquivoDestino,
        $registros,
        $colunas,
        null,
        [
            'utf8_bom' => true,
            'overwrite' => false,
            'protect_csv_injection' => false,
        ]
    );

    if (($resultado['success'] ?? false) !== true) {
        throw new RuntimeException(
            (string) (
                $resultado['mensagem']
                ?? 'Não foi possível salvar o arquivo CSV.'
            )
            . ' Consulte o log export_helper.'
        );
    }

    $caminhoCompleto = rtrim(
        ExportHelper::getBaseDirectory(),
        '/\\'
    ) . '/' . $arquivoDestino;

    if (!is_file($caminhoCompleto)) {
        throw new RuntimeException(
            'O arquivo não foi encontrado após a exportação: '
            . $caminhoCompleto
        );
    }

    fwrite(
        STDOUT,
        'Arquivo salvo com sucesso:' . PHP_EOL
        . $caminhoCompleto . PHP_EOL
    );

    exit(0);
} catch (Throwable $erro) {
    fwrite(
        STDERR,
        'ERRO: ' . $erro->getMessage() . PHP_EOL
    );

    exit(1);
}