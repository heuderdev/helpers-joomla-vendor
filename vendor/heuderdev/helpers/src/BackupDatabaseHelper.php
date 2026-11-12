<?php

// Bloqueia só o acesso direto pela URL; carrega no Joomla, Laravel ou PHP puro.
if (!defined('_JEXEC') && isset($_SERVER['SCRIPT_FILENAME']) && realpath($_SERVER['SCRIPT_FILENAME']) === __FILE__) {
    die('Acesso restrito.');
}

if (!class_exists('PlatformHelper', false)) {
    require_once __DIR__ . '/PlatformHelper.php';
}

if (!class_exists('DbConnectionHelper', false)) {
    require_once __DIR__ . '/DbConnectionHelper.php';
}

/*
 * Backup do banco MySQL/MariaDB em arquivo .sql (ou .sql.gz), em dois modos:
 *
 *   - schema: só a estrutura (tabelas vazias, views e triggers);
 *   - full:   estrutura + todos os dados.
 *
 * Salvar no servidor:
 *
 *   $r = BackupDatabaseHelper::schema();
 *   $r = BackupDatabaseHelper::full(['connection' => 'erp', 'gzip' => true]);
 *   // $r['data']['caminho'] => /var/www/site/backups/database/erp_completo_20261112_101500_a1b2c3.sql.gz
 *
 * Baixar pelo navegador (chame PlatformHelper::close() logo depois, para o
 * Joomla não enviar o template junto com o arquivo):
 *
 *   BackupDatabaseHelper::downloadFull(['gzip' => true, 'save_copy' => true]);
 *   PlatformHelper::close();
 *
 * Opções (todas opcionais):
 *
 *   connection    nome registrado no DbConnectionHelper ou driver; padrão: Joomla
 *   tables        só estas tabelas/views (aceita '#__'); padrão: todas do banco
 *   exclude       tabelas/views que ficam de fora
 *   exclude_data  tabelas que entram só com a estrutura (ex.: '#__session')
 *   prefix_only   true = só tabelas com o prefixo da conexão (ex.: jos_)
 *   views         inclui views (padrão true)
 *   triggers      inclui triggers (padrão true)
 *   gzip          compacta em .sql.gz (padrão false)
 *   consistent    leitura num snapshot único, sem travar tabelas (padrão true)
 *   directory     subpasta dentro do diretório base
 *   file_name     nome do arquivo (a extensão é ajustada)
 *   save_copy     no download, também guarda uma cópia no servidor
 *
 * NUNCA APAGA NADA, nem agora nem ao restaurar:
 *
 *   - no banco, só executa SELECT e SHOW (mais o fuso da sessão e a
 *     transação de leitura, que são desfeitos no final). Qualquer outro
 *     comando é recusado antes de chegar ao banco;
 *   - o arquivo gerado não tem DROP, DELETE, TRUNCATE, REPLACE nem ALTER:
 *     usa CREATE TABLE IF NOT EXISTS e INSERT IGNORE, então restaurar
 *     sobre um banco existente não remove nem sobrescreve registros;
 *   - nunca sobrescreve nem remove arquivos: cada backup ganha um nome
 *     único, e um backup que falha no meio fica como ".parcial" para
 *     análise. Não há método de limpeza de backups antigos de propósito.
 *
 * O backup contém dados sensíveis. A pasta recebe .htaccess, web.config e
 * index.html bloqueando o acesso pela web, mas o ideal é apontar o
 * diretório base para fora da pasta pública com setBaseDirectory(), e
 * checar a permissão do usuário (PermissionHelper) antes de chamar.
 */
class BackupDatabaseHelper
{
    const SCHEMA = 'schema';

    const FULL = 'full';

    private static $baseDirectory = null;

    private static $logCategory = 'backup_database_helper';

    private static $chunkSize = 1000;

    private static $rowsPerInsert = 100;

    private static $maxInsertBytes = 1048576;

    private static $directoryPermission = 0750;

    private static $filePermission = 0640;

    private static $binaryTypes = '/^(binary|varbinary|tinyblob|blob|mediumblob|longblob|bit|geometry|point|linestring|polygon|multipoint|multilinestring|multipolygon|geometrycollection|geomcollection)\b/i';

    // ------------------------------------------------------------------
    // Configuração
    // ------------------------------------------------------------------

    /*
     * Pasta onde os backups são salvos. Caminho absoluto ou relativo à
     * raiz do site. Prefira uma pasta fora da área pública.
     */
    public static function setBaseDirectory($directory)
    {
        self::$baseDirectory = self::prepareDirectory(self::absolutePath($directory));
    }

    public static function getBaseDirectory()
    {
        if (self::$baseDirectory === null) {
            self::$baseDirectory = self::prepareDirectory(
                self::normalizePath(PlatformHelper::rootPath()) . '/backups/database'
            );
        }

        return self::$baseDirectory;
    }

    public static function setLogCategory($category)
    {
        $category = trim((string) $category);

        if ($category !== '') {
            self::$logCategory = $category;
        }
    }

    /*
     * Quantas linhas são lidas do banco por vez (padrão 1000). Diminua
     * para tabelas com BLOBs grandes.
     */
    public static function setChunkSize($rows)
    {
        self::$chunkSize = max(1, (int) $rows);
    }

    /*
     * Quantas linhas vão em cada INSERT do arquivo (padrão 100). Cada
     * INSERT também é limitado a ~1 MB, para caber no max_allowed_packet.
     */
    public static function setRowsPerInsert($rows)
    {
        self::$rowsPerInsert = max(1, (int) $rows);
    }

    // ------------------------------------------------------------------
    // Salvar no servidor
    // ------------------------------------------------------------------

    /*
     * Só a estrutura: tabelas vazias (sem AUTO_INCREMENT acumulado),
     * views e triggers.
     */
    public static function schema(array $options = [])
    {
        return self::save(self::SCHEMA, $options);
    }

    /*
     * Estrutura + dados.
     */
    public static function full(array $options = [])
    {
        return self::save(self::FULL, $options);
    }

    public static function save($mode, array $options = [])
    {
        $started = microtime(true);
        $partial = null;
        $writer = null;

        try {
            $mode = self::normalizeMode($mode);
            $plan = self::plan($mode, $options);

            $directory = self::targetDirectory($options);
            $final = self::uniquePath($directory, self::fileName($plan, $options));
            $partial = $final . '.parcial';

            $writer = self::openWriter([self::createFile($partial)], !empty($options['gzip']));
            $stats = self::dump($plan, $writer);
            self::closeWriter($writer);
            $writer = null;

            // Sem sobrescrever: se outro processo criou o mesmo nome, gera outro.
            if (file_exists($final)) {
                $final = self::uniquePath($directory, basename($final));
            }

            if (!@rename($partial, $final)) {
                throw new RuntimeException('Backup gerado, mas não foi possível renomear o arquivo .parcial.');
            }

            @chmod($final, self::$filePermission);

            $data = self::summary($plan, $stats, $started) + [
                'arquivo' => basename($final),
                'caminho' => $final,
                'bytes' => (int) filesize($final),
            ];

            self::log('info', 'Backup do banco salvo.', $data);

            return self::result(true, 'Backup gerado com sucesso.', $data);
        } catch (Throwable $error) {
            if ($writer !== null) {
                self::markIncomplete($writer, $error);
            }

            $data = ['modo' => $mode, 'arquivo_parcial' => $partial !== null && file_exists($partial) ? $partial : null];

            self::log('error', 'Falha no backup do banco.', $data + ['erro' => $error->getMessage()]);

            return self::result(false, 'Falha ao gerar o backup: ' . $error->getMessage(), $data, [$error->getMessage()]);
        }
    }

    // ------------------------------------------------------------------
    // Download
    // ------------------------------------------------------------------

    public static function downloadSchema(array $options = [])
    {
        return self::download(self::SCHEMA, $options);
    }

    public static function downloadFull(array $options = [])
    {
        return self::download(self::FULL, $options);
    }

    /*
     * Envia o backup direto para o navegador, gerando enquanto lê o banco
     * (não guarda tudo na memória). Com 'save_copy' => true, grava ao
     * mesmo tempo uma cópia no servidor.
     *
     * Erros de validação (tabela inexistente, banco não suportado) voltam
     * no resultado antes de qualquer cabeçalho ser enviado. Se o banco
     * falhar no meio do envio, o arquivo termina com um aviso
     * "BACKUP INCOMPLETO".
     */
    public static function download($mode, array $options = [])
    {
        $started = microtime(true);
        $writer = null;
        $copy = null;

        try {
            $mode = self::normalizeMode($mode);
            $plan = self::plan($mode, $options);
            $fileName = self::fileName($plan, $options);
            $handles = [];

            if (!empty($options['save_copy'])) {
                $copy = self::uniquePath(self::targetDirectory($options), $fileName);
                $handles[] = self::createFile($copy . '.parcial');
            }

            $output = fopen('php://output', 'wb');

            if ($output === false) {
                throw new RuntimeException('Não foi possível abrir a saída para download.');
            }

            $handles[] = $output;

            self::clearOutputBuffers();

            if (!headers_sent()) {
                header('Content-Type: ' . (!empty($options['gzip']) ? 'application/gzip' : 'application/sql; charset=UTF-8'));
                header('Content-Transfer-Encoding: binary');
                header('X-Content-Type-Options: nosniff');
                header('Cache-Control: private, no-store, no-cache, must-revalidate, max-age=0');
                header('Pragma: no-cache');
                header(
                    'Content-Disposition: attachment; filename="' . addslashes($fileName) .
                    '"; filename*=UTF-8\'\'' . rawurlencode($fileName)
                );
            }

            $writer = self::openWriter($handles, !empty($options['gzip']));
            $stats = self::dump($plan, $writer);
            self::closeWriter($writer);
            $writer = null;

            $data = self::summary($plan, $stats, $started) + ['arquivo' => $fileName];

            if ($copy !== null) {
                if (file_exists($copy)) {
                    $copy = self::uniquePath(dirname($copy), basename($copy));
                }

                if (@rename($copy . '.parcial', $copy)) {
                    @chmod($copy, self::$filePermission);
                    $data['copia'] = $copy;
                } else {
                    $data['copia'] = $copy . '.parcial';
                }
            }

            self::log('info', 'Backup do banco baixado.', $data);

            return self::result(true, 'Backup enviado com sucesso.', $data);
        } catch (Throwable $error) {
            if ($writer !== null) {
                self::markIncomplete($writer, $error);
            }

            $data = ['modo' => $mode, 'copia_parcial' => $copy !== null && file_exists($copy . '.parcial') ? $copy . '.parcial' : null];

            self::log('error', 'Falha no download do backup.', $data + ['erro' => $error->getMessage()]);

            return self::result(false, 'Falha ao gerar o backup: ' . $error->getMessage(), $data, [$error->getMessage()]);
        }
    }

    // ------------------------------------------------------------------
    // Consulta
    // ------------------------------------------------------------------

    /*
     * Tabelas e views do banco, com tipo, engine, linhas (estimativa do
     * MySQL) e tamanho. Útil para escolher o que entra no backup.
     */
    public static function tables(array $options = [])
    {
        $db = self::connection($options);
        $list = [];

        foreach (self::query($db, 'SHOW TABLE STATUS', 'loadAssocList') as $row) {
            $view = isset($row['Comment']) && strtoupper((string) $row['Comment']) === 'VIEW' && $row['Engine'] === null;

            $list[] = [
                'nome' => $row['Name'],
                'tipo' => $view ? 'view' : 'tabela',
                'engine' => $row['Engine'],
                'linhas_aprox' => $row['Rows'] === null ? null : (int) $row['Rows'],
                'tamanho_bytes' => (int) $row['Data_length'] + (int) $row['Index_length'],
            ];
        }

        return $list;
    }

    // ------------------------------------------------------------------
    // Planejamento: valida tudo antes de abrir arquivo ou enviar cabeçalho
    // ------------------------------------------------------------------

    private static function plan($mode, array $options)
    {
        $db = self::connection($options);
        $prefix = method_exists($db, 'getPrefix') ? (string) $db->getPrefix() : '';

        $tables = [];
        $views = [];

        foreach (self::query($db, 'SHOW FULL TABLES', 'loadRowList') as $row) {
            if (strtoupper((string) $row[1]) === 'VIEW') {
                $views[] = $row[0];
            } else {
                $tables[] = $row[0];
            }
        }

        $all = array_merge($tables, $views);

        if (!empty($options['tables'])) {
            $wanted = self::names($options['tables'], $prefix);
            $missing = array_diff($wanted, $all);

            if ($missing) {
                throw new InvalidArgumentException('Tabela(s) não encontrada(s): ' . implode(', ', $missing));
            }

            $tables = array_values(array_intersect($tables, $wanted));
            $views = array_values(array_intersect($views, $wanted));
        }

        if (!empty($options['prefix_only']) && $prefix !== '') {
            $hasPrefix = function ($name) use ($prefix) {
                return strpos($name, $prefix) === 0;
            };

            $tables = array_values(array_filter($tables, $hasPrefix));
            $views = array_values(array_filter($views, $hasPrefix));
        }

        if (!empty($options['exclude'])) {
            $exclude = self::names($options['exclude'], $prefix);
            $tables = array_values(array_diff($tables, $exclude));
            $views = array_values(array_diff($views, $exclude));
        }

        if (array_key_exists('views', $options) && !$options['views']) {
            $views = [];
        }

        if (!$tables && !$views) {
            throw new InvalidArgumentException('Nenhuma tabela para o backup.');
        }

        return [
            'db' => $db,
            'mode' => $mode,
            'database' => (string) self::query($db, 'SELECT DATABASE()', 'loadResult'),
            'tables' => $tables,
            'views' => $views,
            'exclude_data' => empty($options['exclude_data']) ? [] : self::names($options['exclude_data'], $prefix),
            'triggers' => !array_key_exists('triggers', $options) || $options['triggers'],
            'consistent' => !array_key_exists('consistent', $options) || $options['consistent'],
        ];
    }

    private static function connection(array $options)
    {
        $db = DbConnectionHelper::resolve(isset($options['connection']) ? $options['connection'] : null);

        if (!DbConnectionHelper::isMysql($db)) {
            throw new RuntimeException('BackupDatabaseHelper suporta apenas MySQL/MariaDB.');
        }

        return $db;
    }

    private static function names($names, $prefix)
    {
        $result = [];

        foreach ((array) $names as $name) {
            $name = trim((string) $name);

            if ($name !== '') {
                $result[] = strpos($name, '#__') === 0 ? $prefix . substr($name, 3) : $name;
            }
        }

        return array_values(array_unique($result));
    }

    private static function normalizeMode($mode)
    {
        $mode = strtolower(trim((string) $mode));

        if ($mode === 'estrutura' || $mode === 'structure') {
            return self::SCHEMA;
        }

        if ($mode === 'completo' || $mode === 'data') {
            return self::FULL;
        }

        if ($mode !== self::SCHEMA && $mode !== self::FULL) {
            throw new InvalidArgumentException('Modo inválido: use BackupDatabaseHelper::SCHEMA ou ::FULL.');
        }

        return $mode;
    }

    // ------------------------------------------------------------------
    // Geração do SQL
    // ------------------------------------------------------------------

    private static function dump(array $plan, array &$writer)
    {
        $db = $plan['db'];

        if (function_exists('set_time_limit')) {
            @set_time_limit(0);
        }

        $stats = ['tabelas' => 0, 'views' => 0, 'triggers' => 0, 'linhas' => 0, 'snapshot' => false];

        // Lê em UTC (TIMESTAMP volta igual em qualquer servidor) e em
        // utf8mb4 (emojis não viram "?"). A sessão volta ao que era no final.
        $session = self::query(
            $db,
            'SELECT @@session.time_zone AS time_zone, @@session.character_set_client AS charset, @@session.collation_connection AS collation',
            'loadAssoc'
        );

        self::session($db, "SET SESSION time_zone = '+00:00'");
        self::session($db, "SET NAMES 'utf8mb4'");

        try {
            if ($plan['consistent'] && self::canUseSnapshot($db)) {
                try {
                    self::session($db, 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ');
                    self::session($db, 'START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY');
                    $stats['snapshot'] = true;
                } catch (Throwable $e) {
                    // Servidor antigo sem READ ONLY: segue sem snapshot.
                }
            }

            try {
                self::write($writer, self::header($plan));

                $triggers = $plan['triggers'] ? self::triggersByTable($db) : [];

                foreach ($plan['tables'] as $table) {
                    self::write($writer, self::createTable($db, $table, $plan['mode']));
                    $stats['tabelas']++;

                    if ($plan['mode'] === self::FULL && !in_array($table, $plan['exclude_data'], true)) {
                        $stats['linhas'] += self::dumpRows($db, $table, $writer);
                    }
                }

                // Triggers depois dos dados (não disparam na restauração) e
                // views por último (dependem das tabelas).
                foreach ($plan['tables'] as $table) {
                    if (isset($triggers[$table])) {
                        foreach ($triggers[$table] as $trigger) {
                            self::write($writer, self::createTrigger($db, $trigger));
                            $stats['triggers']++;
                        }
                    }
                }

                foreach ($plan['views'] as $view) {
                    self::write($writer, self::createView($db, $view));
                    $stats['views']++;
                }

                self::write($writer, self::footer());
            } finally {
                if ($stats['snapshot']) {
                    self::session($db, 'COMMIT');
                }
            }
        } finally {
            self::restoreSession($db, $session);
        }

        return $stats;
    }

    private static function header(array $plan)
    {
        $db = $plan['db'];
        $version = (string) self::query($db, 'SELECT VERSION()', 'loadResult');
        $charset = (string) self::query($db, 'SELECT @@character_set_results', 'loadResult');

        if ($charset === '' || !preg_match('/^\w+$/', $charset)) {
            $charset = 'utf8mb4';
        }

        return "-- Backup gerado por BackupDatabaseHelper\n" .
            '-- Banco: ' . $plan['database'] . "\n" .
            '-- Servidor: ' . $version . "\n" .
            '-- Modo: ' . ($plan['mode'] === self::FULL ? 'estrutura + dados' : 'somente estrutura (tabelas vazias)') . "\n" .
            '-- Gerado em: ' . gmdate('Y-m-d H:i:s') . " UTC\n" .
            "--\n" .
            "-- Este arquivo NÃO apaga nada ao ser restaurado: não há DROP, DELETE,\n" .
            "-- TRUNCATE, REPLACE nem ALTER. Tabelas usam CREATE TABLE IF NOT EXISTS\n" .
            "-- e os dados usam INSERT IGNORE (registros existentes são mantidos).\n" .
            "-- Atenção: tabelas sem chave primária/única recebem as linhas de novo\n" .
            "-- se o arquivo for restaurado duas vezes no mesmo banco.\n\n" .
            "/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;\n" .
            "/*!40101 SET NAMES " . $charset . " */;\n" .
            "/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;\n" .
            "/*!40103 SET TIME_ZONE='+00:00' */;\n" .
            "/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;\n" .
            "/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;\n" .
            "/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;\n\n";
    }

    private static function footer()
    {
        return "/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;\n" .
            "/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;\n" .
            "/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;\n" .
            "/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;\n" .
            "/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;\n\n" .
            "-- Fim do backup\n";
    }

    private static function createTable($db, $table, $mode)
    {
        $row = self::query($db, 'SHOW CREATE TABLE ' . $db->quoteName($table), 'loadRow');
        $sql = isset($row[1]) ? (string) $row[1] : '';

        if (stripos($sql, 'CREATE TABLE ') !== 0) {
            throw new RuntimeException('Não foi possível ler a estrutura da tabela ' . $table . '.');
        }

        $sql = 'CREATE TABLE IF NOT EXISTS ' . substr($sql, strlen('CREATE TABLE '));

        // Tabela vazia começa a contagem do zero.
        if ($mode === self::SCHEMA) {
            $end = strrpos($sql, ')');
            $sql = substr($sql, 0, $end) . preg_replace('/\s+AUTO_INCREMENT=\d+/i', '', substr($sql, $end));
        }

        return "--\n-- Tabela " . $table . "\n--\n\n" . self::safeStatement($sql) . ";\n\n";
    }

    private static function createView($db, $view)
    {
        $row = self::query($db, 'SHOW CREATE VIEW ' . $db->quoteName($view), 'loadRow');
        $sql = isset($row[1]) ? (string) $row[1] : '';

        if ($sql === '') {
            throw new RuntimeException('Não foi possível ler a view ' . $view . '.');
        }

        $sql = self::safeStatement(self::withoutDefiner($sql));

        // IF NOT EXISTS só no MariaDB (o MySQL não tem para views e ignora
        // o comentário /*M! */). No MySQL, view já existente dá erro e não
        // é alterada; por isso as views ficam no fim do arquivo.
        $sql = preg_replace('/^(CREATE\s.*?\bVIEW)\s+/is', '$1 /*M!100103 IF NOT EXISTS */ ', $sql, 1);

        return "--\n-- View " . $view . "\n--\n\n" . $sql . ";\n\n";
    }

    private static function createTrigger($db, $trigger)
    {
        $row = self::query($db, 'SHOW CREATE TRIGGER ' . $db->quoteName($trigger), 'loadAssoc');
        $sql = isset($row['SQL Original Statement']) ? (string) $row['SQL Original Statement'] : '';

        if ($sql === '') {
            throw new RuntimeException('Não foi possível ler o trigger ' . $trigger . '.');
        }

        $sql = self::safeStatement(self::withoutDefiner($sql));

        // IF NOT EXISTS no MySQL 8.0.29+ e no MariaDB; versões antigas ignoram.
        $sql = preg_replace('/^CREATE\s+TRIGGER\s+/i', 'CREATE TRIGGER /*!80029 IF NOT EXISTS */ ', $sql, 1);

        return "--\n-- Trigger " . $trigger . "\n--\n\nDELIMITER ;;\n" . $sql . " ;;\nDELIMITER ;\n\n";
    }

    private static function triggersByTable($db)
    {
        $result = [];

        foreach (self::query($db, 'SHOW TRIGGERS', 'loadAssocList') as $row) {
            $result[$row['Table']][] = $row['Trigger'];
        }

        return $result;
    }

    /*
     * Escreve os INSERTs lendo a tabela em blocos. Com chave primária de
     * uma coluna, pagina pela chave (rápido em tabelas grandes); sem ela,
     * usa LIMIT/OFFSET.
     */
    private static function dumpRows($db, $table, array &$writer)
    {
        $columns = [];
        $kinds = [];
        $expressions = [];

        foreach (self::query($db, 'SHOW FULL COLUMNS FROM ' . $db->quoteName($table), 'loadAssocList') as $column) {
            // Colunas geradas são calculadas pelo banco e não aceitam INSERT.
            if (preg_match('/\b(GENERATED|VIRTUAL|PERSISTENT|STORED)\b/i', (string) $column['Extra'])) {
                continue;
            }

            $quoted = $db->quoteName($column['Field']);
            $type = (string) $column['Type'];

            // Binários vêm em hexadecimal e BIT como número: o driver não
            // devolve esses tipos de forma confiável.
            if (preg_match('/^bit\b/i', $type)) {
                $kinds[] = 'bit';
                $expressions[] = '(' . $quoted . ' + 0)';
            } elseif (preg_match(self::$binaryTypes, $type)) {
                $kinds[] = 'binary';
                $expressions[] = 'HEX(' . $quoted . ')';
            } else {
                $kinds[] = 'text';
                $expressions[] = $quoted;
            }

            $columns[] = $column['Field'];
        }

        if (!$columns) {
            return 0;
        }

        $primary = [];

        foreach (self::query($db, 'SHOW KEYS FROM ' . $db->quoteName($table), 'loadAssocList') as $key) {
            if ($key['Key_name'] === 'PRIMARY') {
                $primary[(int) $key['Seq_in_index']] = $key['Column_name'];
            }
        }

        ksort($primary);
        $primary = array_values($primary);

        $keyIndex = count($primary) === 1 ? array_search($primary[0], $columns, true) : false;

        $quotedTable = $db->quoteName($table);
        $select = 'SELECT ' . implode(', ', $expressions) . ' FROM ' . $quotedTable;
        $insert = 'INSERT IGNORE INTO ' . $quotedTable . ' (' . implode(', ', array_map([$db, 'quoteName'], $columns)) . ') VALUES';

        $total = 0;
        $offset = 0;
        $lastKey = null;
        $values = [];
        $size = 0;

        self::write($writer, "--\n-- Dados de " . $table . "\n--\n\n");

        while (true) {
            if ($keyIndex !== false) {
                $sql = $select .
                    ($lastKey === null ? '' : ' WHERE ' . $db->quoteName($primary[0]) . ' > ' . $lastKey) .
                    ' ORDER BY ' . $db->quoteName($primary[0]) .
                    ' LIMIT ' . self::$chunkSize;
            } else {
                $sql = $select .
                    ($primary ? ' ORDER BY ' . implode(', ', array_map([$db, 'quoteName'], $primary)) : '') .
                    ' LIMIT ' . $offset . ', ' . self::$chunkSize;
            }

            $rows = self::query($db, $sql, 'loadRowList');

            if (!$rows) {
                break;
            }

            foreach ($rows as $row) {
                $encoded = [];

                foreach ($row as $i => $value) {
                    $encoded[] = self::encodeValue($db, $value, $kinds[$i]);
                }

                $tuple = '(' . implode(',', $encoded) . ')';
                $values[] = $tuple;
                $size += strlen($tuple);

                if (count($values) >= self::$rowsPerInsert || $size >= self::$maxInsertBytes) {
                    self::write($writer, $insert . "\n" . implode(",\n", $values) . ";\n");
                    $values = [];
                    $size = 0;
                }

                if ($keyIndex !== false) {
                    $lastKey = $encoded[$keyIndex];
                }
            }

            $total += count($rows);
            $offset += count($rows);

            if (count($rows) < self::$chunkSize) {
                break;
            }
        }

        if ($values) {
            self::write($writer, $insert . "\n" . implode(",\n", $values) . ";\n");
        }

        self::write($writer, "\n");

        return $total;
    }

    private static function encodeValue($db, $value, $kind)
    {
        if ($value === null) {
            return 'NULL';
        }

        $value = (string) $value;

        if ($kind === 'binary') {
            if (!preg_match('/^[0-9A-Fa-f]*$/', $value)) {
                throw new RuntimeException('Valor binário inesperado na leitura do banco.');
            }

            return $value === '' ? "''" : '0x' . $value;
        }

        if ($kind === 'bit') {
            if (!ctype_digit($value)) {
                throw new RuntimeException('Valor BIT inesperado na leitura do banco.');
            }

            return $value;
        }

        return $db->quote($value);
    }

    private static function withoutDefiner($sql)
    {
        return preg_replace('/\s+DEFINER\s*=\s*(`[^`]*`|\'[^\']*\'|\S+?)@(`[^`]*`|\'[^\']*\'|\S+)/i', '', $sql, 1);
    }

    /*
     * Última barreira: nenhum comando do arquivo pode começar com algo
     * que apague ou altere dados existentes.
     */
    private static function safeStatement($sql)
    {
        $sql = trim((string) $sql);

        if (!preg_match('/^CREATE\s+(TABLE\s+IF\s+NOT\s+EXISTS|(ALGORITHM\s*=\s*\w+\s+)?(SQL\s+SECURITY\s+\w+\s+)?VIEW|TRIGGER)\s/i', $sql)) {
            throw new LogicException('Comando inesperado no backup: ' . substr($sql, 0, 60));
        }

        return $sql;
    }

    // ------------------------------------------------------------------
    // Acesso ao banco: somente leitura
    // ------------------------------------------------------------------

    /*
     * Toda leitura passa por aqui, e só SELECT e SHOW são aceitos.
     */
    private static function query($db, $sql, $method)
    {
        if (!preg_match('/^\s*(SELECT|SHOW)\s/i', $sql)) {
            throw new LogicException('BackupDatabaseHelper só executa SELECT e SHOW.');
        }

        $db->setQuery($sql);
        $result = $db->$method();

        return $result === null && ($method === 'loadAssocList' || $method === 'loadRowList') ? [] : $result;
    }

    /*
     * Comandos de sessão permitidos: fuso horário da conexão e a transação
     * de leitura do snapshot. Nada além disso.
     */
    private static function restoreSession($db, $session)
    {
        $value = function ($key, $pattern, $default) use ($session) {
            $text = is_array($session) && isset($session[$key]) ? (string) $session[$key] : '';

            return preg_match($pattern, $text) ? $text : $default;
        };

        $charset = $value('charset', '/^\w+$/', 'utf8mb4');
        $collation = $value('collation', '/^\w+$/', '');

        self::session($db, "SET NAMES '" . $charset . "'" . ($collation !== '' ? " COLLATE '" . $collation . "'" : ''));
        self::session($db, "SET SESSION time_zone = '" . $value('time_zone', '/^[\w\/:+-]+$/', 'SYSTEM') . "'");
    }

    private static function session($db, $sql)
    {
        $allowed = '/^(SET SESSION time_zone = \'[\w\/:+-]+\'|SET NAMES \'\w+\'( COLLATE \'\w+\')?|SET TRANSACTION ISOLATION LEVEL REPEATABLE READ|START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY|COMMIT)$/';

        if (!preg_match($allowed, $sql)) {
            throw new LogicException('Comando de sessão não permitido no backup.');
        }

        // O driver do Joomla usa prepared statements, e o MySQL não aceita
        // START TRANSACTION nesse modo; por isso vai pela conexão nativa.
        if (method_exists($db, 'connect')) {
            $db->connect();
        }

        $connection = method_exists($db, 'getConnection') ? $db->getConnection() : null;

        if ($connection instanceof mysqli) {
            // O mysqli só escapa no charset certo se souber da troca.
            if (preg_match('/^SET NAMES \'(\w+)\'/', $sql, $match)) {
                try {
                    $connection->set_charset($match[1]);
                } catch (Throwable $e) {
                }
            }

            if ($connection->query($sql) === false) {
                throw new RuntimeException($connection->error);
            }

            return;
        }

        if ($connection instanceof PDO) {
            $connection->exec($sql);

            return;
        }

        $db->setQuery($sql);
        $db->execute();
    }

    /*
     * START TRANSACTION confirmaria uma transação já aberta na mesma
     * conexão. Por isso o snapshot só é usado quando não há nenhuma.
     */
    private static function canUseSnapshot($db)
    {
        if (class_exists('DbTransactionHelper') && DbTransactionHelper::isActive($db)) {
            return false;
        }

        try {
            $property = new ReflectionProperty($db, 'transactionDepth');

            if ((int) $property->getValue($db) > 0) {
                return false;
            }
        } catch (Throwable $e) {
        }

        // MariaDB informa se a sessão está dentro de uma transação.
        try {
            if ((int) self::query($db, 'SELECT @@in_transaction', 'loadResult') > 0) {
                return false;
            }
        } catch (Throwable $e) {
        }

        return true;
    }

    // ------------------------------------------------------------------
    // Saída (arquivo e/ou navegador), com gzip opcional
    // ------------------------------------------------------------------

    private static function openWriter(array $handles, $gzip)
    {
        $deflate = null;

        if ($gzip) {
            if (!function_exists('deflate_init')) {
                throw new RuntimeException('A extensão zlib é necessária para gerar .sql.gz.');
            }

            $deflate = deflate_init(ZLIB_ENCODING_GZIP, ['level' => 6]);
        }

        return ['handles' => $handles, 'deflate' => $deflate, 'buffer' => ''];
    }

    private static function write(array &$writer, $text)
    {
        $writer['buffer'] .= $text;

        if (strlen($writer['buffer']) >= 65536) {
            self::flush($writer, false);
        }
    }

    private static function flush(array &$writer, $finish)
    {
        $data = $writer['buffer'];
        $writer['buffer'] = '';

        if ($writer['deflate'] !== null) {
            $data = deflate_add($writer['deflate'], $data, $finish ? ZLIB_FINISH : ZLIB_NO_FLUSH);
        }

        if ($data === '' || $data === false) {
            return;
        }

        foreach ($writer['handles'] as $handle) {
            $length = strlen($data);
            $written = 0;

            while ($written < $length) {
                $bytes = fwrite($handle, $written === 0 ? $data : substr($data, $written));

                if ($bytes === false || $bytes === 0) {
                    throw new RuntimeException('Falha ao gravar o backup (disco cheio ou conexão encerrada?).');
                }

                $written += $bytes;
            }
        }

        if (!$finish) {
            foreach ($writer['handles'] as $handle) {
                fflush($handle);
            }
        }
    }

    private static function closeWriter(array &$writer)
    {
        self::flush($writer, true);

        foreach ($writer['handles'] as $handle) {
            fclose($handle);
        }
    }

    private static function markIncomplete(array &$writer, Throwable $error)
    {
        try {
            $writer['buffer'] .= "\n\n-- ATENÇÃO: BACKUP INCOMPLETO. Não use este arquivo para restaurar.\n" .
                '-- Erro: ' . str_replace(["\r", "\n"], ' ', $error->getMessage()) . "\n";
            self::closeWriter($writer);
        } catch (Throwable $e) {
        }
    }

    // ------------------------------------------------------------------
    // Arquivos: nunca sobrescreve nem remove
    // ------------------------------------------------------------------

    private static function createFile($path)
    {
        // Modo 'x': falha se o arquivo já existir.
        $handle = @fopen($path, 'xb');

        if ($handle === false) {
            throw new RuntimeException('Não foi possível criar o arquivo de backup: ' . basename($path));
        }

        @chmod($path, self::$filePermission);

        return $handle;
    }

    private static function fileName(array $plan, array $options)
    {
        $extension = !empty($options['gzip']) ? '.sql.gz' : '.sql';

        if (!empty($options['file_name'])) {
            $name = preg_replace('/(\.sql)?(\.gz)?$/i', '', basename(str_replace('\\', '/', (string) $options['file_name'])));
            $name = PlatformHelper::makeSafe($name);

            if ($name !== '') {
                return $name . $extension;
            }
        }

        $database = PlatformHelper::makeSafe($plan['database']);

        return ($database !== '' ? $database : 'banco') . '_' .
            ($plan['mode'] === self::FULL ? 'completo' : 'estrutura') . '_' .
            date('Ymd_His') . '_' . bin2hex(random_bytes(3)) . $extension;
    }

    private static function uniquePath($directory, $fileName)
    {
        $path = $directory . '/' . $fileName;

        while (file_exists($path) || file_exists($path . '.parcial')) {
            $base = preg_replace('/(\.sql(\.gz)?)$/i', '', $fileName);
            $extension = substr($fileName, strlen($base));
            $path = $directory . '/' . $base . '_' . bin2hex(random_bytes(3)) . $extension;
        }

        return $path;
    }

    private static function targetDirectory(array $options)
    {
        $base = self::getBaseDirectory();

        if (empty($options['directory'])) {
            return $base;
        }

        $parts = [];

        foreach (explode('/', trim(str_replace('\\', '/', (string) $options['directory']), '/')) as $part) {
            $part = trim($part);

            if ($part === '' || $part === '.' || $part === '..' || strpos($part, "\0") !== false) {
                throw new InvalidArgumentException('Subpasta de backup inválida.');
            }

            $parts[] = PlatformHelper::makeSafe($part);
        }

        return self::prepareDirectory($base . '/' . implode('/', $parts));
    }

    private static function absolutePath($directory)
    {
        $directory = trim(str_replace('\\', '/', (string) $directory));

        if ($directory === '' || strpos($directory, "\0") !== false || preg_match('#(^|/)\.\.(/|$)#', $directory)) {
            throw new InvalidArgumentException('Diretório de backup inválido.');
        }

        if ($directory[0] !== '/' && !preg_match('#^[A-Za-z]:/#', $directory)) {
            $directory = self::normalizePath(PlatformHelper::rootPath()) . '/' . $directory;
        }

        return self::normalizePath($directory);
    }

    /*
     * Cria a pasta se preciso e bloqueia o acesso pela web. Os arquivos de
     * proteção só são criados quando não existem.
     */
    private static function prepareDirectory($directory)
    {
        if (!is_dir($directory) && !@mkdir($directory, self::$directoryPermission, true) && !is_dir($directory)) {
            throw new RuntimeException('Não foi possível criar a pasta de backup: ' . $directory);
        }

        $real = realpath($directory);

        if ($real === false || !is_writable($real)) {
            throw new RuntimeException('Sem permissão de escrita na pasta de backup: ' . $directory);
        }

        $real = self::normalizePath($real);

        $protection = [
            '.htaccess' => "<IfModule mod_authz_core.c>\n    Require all denied\n</IfModule>\n<IfModule !mod_authz_core.c>\n    Deny from all\n</IfModule>\n",
            'web.config' => "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<configuration>\n  <system.webServer>\n    <authorization>\n      <deny users=\"*\" />\n    </authorization>\n  </system.webServer>\n</configuration>\n",
            'index.html' => "<!DOCTYPE html><title></title>\n",
        ];

        foreach ($protection as $name => $content) {
            if (!file_exists($real . '/' . $name)) {
                $handle = @fopen($real . '/' . $name, 'xb');

                if ($handle !== false) {
                    fwrite($handle, $content);
                    fclose($handle);
                }
            }
        }

        return $real;
    }

    private static function normalizePath($path)
    {
        return rtrim(preg_replace('#/+#', '/', str_replace('\\', '/', (string) $path)), '/');
    }

    private static function clearOutputBuffers()
    {
        while (ob_get_level() > 0) {
            ob_end_clean();
        }
    }

    // ------------------------------------------------------------------
    // Resultado e log
    // ------------------------------------------------------------------

    private static function summary(array $plan, array $stats, $started)
    {
        return [
            'modo' => $plan['mode'],
            'banco' => $plan['database'],
            'tabelas' => $stats['tabelas'],
            'views' => $stats['views'],
            'triggers' => $stats['triggers'],
            'linhas' => $stats['linhas'],
            'snapshot' => $stats['snapshot'],
            'segundos' => round(microtime(true) - $started, 2),
        ];
    }

    private static function result($success, $message, array $data = [], array $errors = [])
    {
        return [
            'success' => (bool) $success,
            'status' => $success ? 'sucesso' : 'erro',
            'mensagem' => (string) $message,
            'data' => $data,
            'errors' => $errors,
        ];
    }

    private static function log($level, $message, array $context = [])
    {
        try {
            $levels = [
                'info' => PlatformHelper::LOG_INFO,
                'warning' => PlatformHelper::LOG_WARNING,
                'error' => PlatformHelper::LOG_ERROR,
            ];

            try {
                $context['usuario'] = PlatformHelper::userId();
            } catch (Throwable $e) {
            }

            $json = json_encode($context, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);

            PlatformHelper::log(
                $message . ($json ? ' | Contexto: ' . $json : ''),
                isset($levels[$level]) ? $levels[$level] : PlatformHelper::LOG_INFO,
                self::$logCategory
            );
        } catch (Throwable $e) {
        }
    }
}
