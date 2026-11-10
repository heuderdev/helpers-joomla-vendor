<?php

// Bloqueia só o acesso direto pela URL; carrega no Joomla, Laravel ou PHP puro.
if (!defined('_JEXEC') && isset($_SERVER['SCRIPT_FILENAME']) && realpath($_SERVER['SCRIPT_FILENAME']) === __FILE__) {
    die('Acesso restrito.');
}

/*
 * Manipulação de arrays e objetos com caminho em notação de ponto.
 *
 *   ArrayHelper::get($dados, 'cliente.endereco.cidade', '-');
 *   ArrayHelper::get($pedido, 'itens.*.preco');      // [10, 20, 30]
 *   ArrayHelper::set($dados, 'cliente.nome', 'Ana');
 *   ArrayHelper::pluck($pedidos, 'id');
 *   ArrayHelper::groupBy($pedidos, 'status');
 *
 * Caminho: 'a.b.c', inteiro (3) ou lista de segmentos (['a', 'b.c']) para
 * chaves que contêm ponto. Se a chave literal 'a.b' existir, ela vence.
 * Em get/has, o segmento '*' percorre todos os itens daquele nível.
 *
 * Funciona com arrays, stdClass, objetos com propriedades públicas,
 * objetos com __get/__isset (Joomla CMSObject/Table) e ArrayAccess
 * (ArrayObject, Joomla Registry).
 */

class ArrayHelper
{
    // ------------------------------------------------------------------
    // Conversão
    // ------------------------------------------------------------------

    /*
     * Array (ou objeto) para stdClass. Com $recursivo, sub-arrays
     * associativos viram objetos; listas continuam arrays se
     * $preservarListas for true.
     */
    public static function toObject(
        $dados,
        $recursivo = false,
        $preservarListas = true
    ) {
        if (is_object($dados)) {
            if (!$recursivo) {
                return $dados;
            }

            $dados = self::toArray($dados);
        }

        if (!is_array($dados)) {
            throw new InvalidArgumentException(
                'Os dados devem ser um array ou objeto.'
            );
        }

        if (!$recursivo) {
            return (object) $dados;
        }

        $objeto = new stdClass();

        foreach ($dados as $chave => $valor) {
            $objeto->{$chave} = self::convertValueToObject(
                $valor,
                $preservarListas
            );
        }

        return $objeto;
    }

    /*
     * Lista de registros (arrays) para lista de objetos, mantendo as chaves.
     */
    public static function toObjects(
        array $registros,
        $recursivo = false,
        $preservarListas = true
    ) {
        $resultado = [];

        foreach ($registros as $chave => $registro) {
            if (is_array($registro)) {
                $registro = self::toObject(
                    $registro,
                    $recursivo,
                    $preservarListas
                );
            } elseif (!is_object($registro)) {
                throw new InvalidArgumentException(
                    'Cada registro deve ser um array ou objeto.'
                );
            } elseif ($recursivo) {
                $registro = self::toObject(
                    $registro,
                    true,
                    $preservarListas
                );
            }

            $resultado[$chave] = $registro;
        }

        return $resultado;
    }

    /*
     * Array ou qualquer objeto para array. Aceita stdClass, objetos com
     * toArray() (Registry, models), Traversable, JsonSerializable e objetos
     * comuns (propriedades públicas).
     *
     * Com $recursivo, converte também os níveis internos. Datas, enums e
     * closures são mantidos como estão; referências circulares também,
     * para não entrar em loop.
     */
    public static function toArray($dados, $recursivo = false)
    {
        $pilha = [];

        return self::toArrayInternal($dados, (bool) $recursivo, $pilha);
    }

    /*
     * true para [0 => .., 1 => .., 2 => ..] (chaves sequenciais desde 0).
     */
    public static function isList(array $dados)
    {
        if (function_exists('array_is_list')) {
            return array_is_list($dados);
        }

        $indice = 0;

        foreach ($dados as $chave => $valor) {
            if ($chave !== $indice) {
                return false;
            }

            $indice++;
        }

        return true;
    }

    // ------------------------------------------------------------------
    // Acesso por caminho
    // ------------------------------------------------------------------

    /*
     * Valor no caminho ou $padrao. $padrao pode ser uma Closure, chamada
     * só quando necessário.
     */
    public static function get($dados, $caminho, $padrao = null)
    {
        if ($caminho === null) {
            return $dados;
        }

        $valor = null;

        if (self::readLiteral($dados, $caminho, $valor)) {
            return $valor;
        }

        return self::getBySegments($dados, self::segments($caminho), $padrao);
    }

    /*
     * true se o caminho existir, mesmo que o valor seja null.
     * Com '*', todos os itens daquele nível precisam ter o restante.
     */
    public static function has($dados, $caminho)
    {
        if ($caminho === null) {
            return false;
        }

        $valor = null;

        if (self::readLiteral($dados, $caminho, $valor)) {
            return true;
        }

        return self::hasBySegments($dados, self::segments($caminho));
    }

    /*
     * Grava $valor no caminho, criando os níveis que faltarem (array
     * dentro de array, stdClass dentro de objeto). Altera $dados e
     * também o devolve.
     */
    public static function set(&$dados, $caminho, $valor)
    {
        if ($caminho === null) {
            $dados = $valor;

            return $dados;
        }

        if ($dados === null) {
            $dados = [];
        }

        self::assertContainer($dados);

        if (is_string($caminho) && strpos($caminho, '.') !== false) {
            $encontrado = false;
            self::readKey($dados, $caminho, $encontrado);

            if ($encontrado) {
                self::writeKey($dados, $caminho, $valor);

                return $dados;
            }
        }

        self::setBySegments($dados, self::segments($caminho), $valor);

        return $dados;
    }

    /*
     * Remove o caminho, se existir. Altera $dados e também o devolve.
     */
    public static function forget(&$dados, $caminho)
    {
        if ($caminho === null || (!is_array($dados) && !is_object($dados))) {
            return $dados;
        }

        if (is_string($caminho) && strpos($caminho, '.') !== false) {
            $encontrado = false;
            self::readKey($dados, $caminho, $encontrado);

            if ($encontrado) {
                self::unsetKey($dados, $caminho);

                return $dados;
            }
        }

        self::forgetBySegments($dados, self::segments($caminho));

        return $dados;
    }

    // ------------------------------------------------------------------
    // Seleção de chaves
    // ------------------------------------------------------------------

    /*
     * Só as chaves pedidas (aceita caminhos com ponto). Devolve array.
     */
    public static function only($dados, $chaves)
    {
        $resultado = [];

        foreach ((array) $chaves as $chave) {
            $valor = null;

            if (self::readLiteral($dados, $chave, $valor)) {
                $resultado[$chave] = $valor;
                continue;
            }

            if (self::has($dados, $chave)) {
                self::set($resultado, $chave, self::get($dados, $chave));
            }
        }

        return $resultado;
    }

    /*
     * Tudo menos as chaves informadas (aceita caminhos com ponto).
     * Objetos são clonados (cópia rasa) para não alterar o original.
     */
    public static function except($dados, $chaves)
    {
        if (is_object($dados)) {
            $dados = clone $dados;
        }

        foreach ((array) $chaves as $chave) {
            self::forget($dados, $chave);
        }

        return $dados;
    }

    // ------------------------------------------------------------------
    // Coleções de registros
    // ------------------------------------------------------------------

    /*
     * Valores de um campo em cada registro. $caminho e $chavePor aceitam
     * caminho ou Closure($registro, $chave).
     */
    public static function pluck(
        array $registros,
        $caminho,
        $chavePor = null
    ) {
        $resultado = [];

        foreach ($registros as $indice => $registro) {
            $valor = self::resolve($registro, $indice, $caminho);

            if ($chavePor === null) {
                $resultado[] = $valor;
                continue;
            }

            if (!($chavePor instanceof Closure) && !self::has($registro, $chavePor)) {
                continue;
            }

            $chave = self::normalizeIndex(
                self::resolve($registro, $indice, $chavePor)
            );

            $resultado[$chave] = $valor;
        }

        return $resultado;
    }

    /*
     * Indexa os registros pelo valor do campo (o último vence).
     */
    public static function keyBy(array $registros, $caminho)
    {
        $resultado = [];

        foreach ($registros as $indice => $registro) {
            if (!($caminho instanceof Closure) && !self::has($registro, $caminho)) {
                continue;
            }

            $chave = self::normalizeIndex(
                self::resolve($registro, $indice, $caminho)
            );

            $resultado[$chave] = $registro;
        }

        return $resultado;
    }

    /*
     * Agrupa os registros pelo valor do campo. Null vira '', bool vira 0/1,
     * enums e datas viram texto.
     */
    public static function groupBy(
        array $registros,
        $caminho,
        $preservarChaves = false
    ) {
        $resultado = [];

        foreach ($registros as $indice => $registro) {
            if (!($caminho instanceof Closure) && !self::has($registro, $caminho)) {
                continue;
            }

            $chave = self::normalizeIndex(
                self::resolve($registro, $indice, $caminho)
            );

            if (!array_key_exists($chave, $resultado)) {
                $resultado[$chave] = [];
            }

            if ($preservarChaves) {
                $resultado[$chave][$indice] = $registro;
            } else {
                $resultado[$chave][] = $registro;
            }
        }

        return $resultado;
    }

    /*
     * Primeiro item (ou o primeiro que passar no filtro).
     */
    public static function first(
        array $dados,
        ?callable $filtro = null,
        $padrao = null
    ) {
        foreach ($dados as $chave => $valor) {
            if ($filtro === null || $filtro($valor, $chave)) {
                return $valor;
            }
        }

        return self::value($padrao);
    }

    /*
     * Registros cujo campo é igual a $valor. Por padrão a comparação é
     * estrita (===); passe $estrito = false para comparar '5' com 5, como
     * acontece com valores vindos do banco.
     */
    public static function where(
        array $registros,
        $caminho,
        $valor,
        $preservarChaves = false,
        $estrito = true
    ) {
        $resultado = [];

        foreach ($registros as $chave => $registro) {
            if (!self::has($registro, $caminho)) {
                continue;
            }

            $atual = self::get($registro, $caminho);

            if ($estrito ? $atual !== $valor : $atual != $valor) {
                continue;
            }

            if ($preservarChaves) {
                $resultado[$chave] = $registro;
            } else {
                $resultado[] = $registro;
            }
        }

        return $resultado;
    }

    // ------------------------------------------------------------------
    // Internos
    // ------------------------------------------------------------------

    private static function convertValueToObject(
        $valor,
        $preservarListas
    ) {
        if (!is_array($valor)) {
            return $valor;
        }

        if ($preservarListas && self::isList($valor)) {
            $resultado = [];

            foreach ($valor as $chave => $item) {
                $resultado[$chave] = self::convertValueToObject(
                    $item,
                    $preservarListas
                );
            }

            return $resultado;
        }

        return self::toObject($valor, true, $preservarListas);
    }

    private static function toArrayInternal($dados, $recursivo, array &$pilha)
    {
        $id = is_object($dados) ? spl_object_id($dados) : null;

        if ($id !== null) {
            $pilha[$id] = true;
        }

        $dados = self::objectToArray($dados);

        if ($recursivo) {
            foreach ($dados as $chave => $valor) {
                if (is_array($valor)) {
                    $dados[$chave] = self::toArrayInternal($valor, true, $pilha);
                } elseif (
                    is_object($valor)
                    && self::isConvertibleObject($valor)
                    && !isset($pilha[spl_object_id($valor)])
                ) {
                    $dados[$chave] = self::toArrayInternal($valor, true, $pilha);
                }
            }
        }

        if ($id !== null) {
            unset($pilha[$id]);
        }

        return $dados;
    }

    private static function objectToArray($dados)
    {
        if (is_array($dados)) {
            return $dados;
        }

        if (!is_object($dados)) {
            throw new InvalidArgumentException(
                'Os dados devem ser um array ou objeto.'
            );
        }

        if ($dados instanceof stdClass) {
            return get_object_vars($dados);
        }

        if (is_callable([$dados, 'toArray'])) {
            $convertido = $dados->toArray();

            if (is_array($convertido)) {
                return $convertido;
            }
        }

        if ($dados instanceof Traversable) {
            return iterator_to_array($dados, true);
        }

        if ($dados instanceof JsonSerializable) {
            $convertido = $dados->jsonSerialize();

            if (is_array($convertido)) {
                return $convertido;
            }

            if (is_object($convertido)) {
                return get_object_vars($convertido);
            }
        }

        return get_object_vars($dados);
    }

    /*
     * Objetos que representam um valor (e não uma estrutura) ficam como estão.
     */
    private static function isConvertibleObject($objeto)
    {
        return !($objeto instanceof DateTimeInterface)
            && !($objeto instanceof UnitEnum)
            && !($objeto instanceof Closure);
    }

    private static function segments($caminho)
    {
        if (is_array($caminho)) {
            $segmentos = array_values($caminho);
        } elseif (is_string($caminho) || is_int($caminho)) {
            $segmentos = explode('.', (string) $caminho);
        } else {
            throw new InvalidArgumentException(
                'O caminho deve ser uma string, inteiro ou array.'
            );
        }

        if ($segmentos === []) {
            throw new InvalidArgumentException(
                'O caminho deve conter pelo menos uma chave.'
            );
        }

        foreach ($segmentos as $segmento) {
            self::validateIndex($segmento);
        }

        return $segmentos;
    }

    private static function readLiteral($dados, $caminho, &$valor)
    {
        if (!is_string($caminho) || strpos($caminho, '.') === false) {
            return false;
        }

        $encontrado = false;
        $valor = self::readKey($dados, $caminho, $encontrado);

        return $encontrado;
    }

    private static function getBySegments($dados, array $segmentos, $padrao)
    {
        while ($segmentos !== []) {
            $segmento = array_shift($segmentos);

            if ($segmento === '*') {
                $itens = self::iterable($dados);

                if ($itens === null) {
                    return self::value($padrao);
                }

                $resultado = [];
                $aninhado = in_array('*', $segmentos, true);

                foreach ($itens as $item) {
                    $valor = $segmentos === []
                        ? $item
                        : self::getBySegments($item, $segmentos, $padrao);

                    if ($aninhado && is_array($valor)) {
                        foreach ($valor as $subitem) {
                            $resultado[] = $subitem;
                        }
                    } else {
                        $resultado[] = $valor;
                    }
                }

                return $resultado;
            }

            $encontrado = false;
            $dados = self::readKey($dados, $segmento, $encontrado);

            if (!$encontrado) {
                return self::value($padrao);
            }
        }

        return $dados;
    }

    private static function hasBySegments($dados, array $segmentos)
    {
        while ($segmentos !== []) {
            $segmento = array_shift($segmentos);

            if ($segmento === '*') {
                $itens = self::iterable($dados);

                if ($itens === null) {
                    return false;
                }

                $algum = false;

                foreach ($itens as $item) {
                    $algum = true;

                    if ($segmentos !== [] && !self::hasBySegments($item, $segmentos)) {
                        return false;
                    }
                }

                return $algum;
            }

            $encontrado = false;
            $dados = self::readKey($dados, $segmento, $encontrado);

            if (!$encontrado) {
                return false;
            }
        }

        return true;
    }

    private static function setBySegments(&$alvo, array $segmentos, $valor)
    {
        $segmento = array_shift($segmentos);

        if ($segmentos === []) {
            self::writeKey($alvo, $segmento, $valor);

            return;
        }

        // Arrays: por referência, sem copiar o nível inteiro.
        if (is_array($alvo)) {
            if (
                !array_key_exists($segmento, $alvo)
                || (!is_array($alvo[$segmento]) && !is_object($alvo[$segmento]))
            ) {
                $alvo[$segmento] = [];
            }

            self::setBySegments($alvo[$segmento], $segmentos, $valor);

            return;
        }

        $encontrado = false;
        $filho = self::readKey($alvo, $segmento, $encontrado);

        if (is_object($filho)) {
            // Objeto é passado por handle: basta alterar.
            self::setBySegments($filho, $segmentos, $valor);

            return;
        }

        if (!$encontrado || !is_array($filho)) {
            $filho = $alvo instanceof ArrayAccess ? [] : new stdClass();
        }

        self::setBySegments($filho, $segmentos, $valor);
        self::writeKey($alvo, $segmento, $filho);
    }

    private static function forgetBySegments(&$alvo, array $segmentos)
    {
        $segmento = array_shift($segmentos);

        if ($segmentos === []) {
            self::unsetKey($alvo, $segmento);

            return;
        }

        if (is_array($alvo)) {
            if (
                array_key_exists($segmento, $alvo)
                && (is_array($alvo[$segmento]) || is_object($alvo[$segmento]))
            ) {
                self::forgetBySegments($alvo[$segmento], $segmentos);
            }

            return;
        }

        $encontrado = false;
        $filho = self::readKey($alvo, $segmento, $encontrado);

        if (!$encontrado) {
            return;
        }

        if (is_object($filho)) {
            self::forgetBySegments($filho, $segmentos);
        } elseif (is_array($filho)) {
            self::forgetBySegments($filho, $segmentos);
            self::writeKey($alvo, $segmento, $filho);
        }
    }

    private static function readKey($dados, $chave, &$encontrado)
    {
        $encontrado = false;

        if (is_array($dados)) {
            if (array_key_exists($chave, $dados)) {
                $encontrado = true;

                return $dados[$chave];
            }

            return null;
        }

        if (!is_object($dados)) {
            return null;
        }

        if ($dados instanceof ArrayAccess && $dados->offsetExists($chave)) {
            $encontrado = true;

            return $dados[$chave];
        }

        $propriedades = get_object_vars($dados);

        if (array_key_exists($chave, $propriedades)) {
            $encontrado = true;

            return $propriedades[$chave];
        }

        // Propriedades mágicas (__isset/__get).
        if (isset($dados->{$chave})) {
            $encontrado = true;

            return $dados->{$chave};
        }

        return null;
    }

    private static function writeKey(&$alvo, $chave, $valor)
    {
        if (is_array($alvo)) {
            $alvo[$chave] = $valor;
        } elseif ($alvo instanceof ArrayAccess) {
            $alvo[$chave] = $valor;
        } elseif (is_object($alvo)) {
            $alvo->{$chave} = $valor;
        } else {
            throw new InvalidArgumentException(
                'Não é possível gravar a chave "' . $chave . '" em um valor escalar.'
            );
        }
    }

    private static function unsetKey(&$alvo, $chave)
    {
        if (is_array($alvo)) {
            unset($alvo[$chave]);
        } elseif ($alvo instanceof ArrayAccess) {
            if ($alvo->offsetExists($chave)) {
                unset($alvo[$chave]);
            } else {
                unset($alvo->{$chave});
            }
        } elseif (is_object($alvo)) {
            unset($alvo->{$chave});
        }
    }

    private static function iterable($dados)
    {
        if (is_array($dados) || $dados instanceof Traversable) {
            return $dados;
        }

        if (is_object($dados)) {
            return get_object_vars($dados);
        }

        return null;
    }

    private static function assertContainer($dados)
    {
        if (!is_array($dados) && !is_object($dados)) {
            throw new InvalidArgumentException(
                'Os dados devem ser um array ou objeto.'
            );
        }
    }

    private static function resolve($registro, $indice, $caminho)
    {
        if ($caminho instanceof Closure) {
            return $caminho($registro, $indice);
        }

        return self::get($registro, $caminho);
    }

    private static function value($valor)
    {
        return $valor instanceof Closure ? $valor() : $valor;
    }

    /*
     * Converte o valor de um campo em chave de array válida.
     */
    private static function normalizeIndex($chave)
    {
        if (is_int($chave) || is_string($chave)) {
            return $chave;
        }

        if ($chave === null) {
            return '';
        }

        if (is_bool($chave)) {
            return (int) $chave;
        }

        if (is_float($chave)) {
            return (string) $chave;
        }

        if ($chave instanceof BackedEnum) {
            return $chave->value;
        }

        if ($chave instanceof UnitEnum) {
            return $chave->name;
        }

        if ($chave instanceof DateTimeInterface) {
            return $chave->format('Y-m-d H:i:s');
        }

        if (is_object($chave) && method_exists($chave, '__toString')) {
            return (string) $chave;
        }

        throw new InvalidArgumentException(
            'O valor não pode ser usado como chave (use string, número, bool, null, enum ou data).'
        );
    }

    private static function validateIndex($chave)
    {
        if (!is_string($chave) && !is_int($chave)) {
            throw new InvalidArgumentException(
                'A chave deve ser uma string ou inteiro.'
            );
        }
    }
}
