<?php

// Bloqueia só o acesso direto pela URL; carrega no Joomla, Laravel ou PHP puro.
if (!defined('_JEXEC') && isset($_SERVER['SCRIPT_FILENAME']) && realpath($_SERVER['SCRIPT_FILENAME']) === __FILE__) {
    die('Acesso restrito.');
}

/*
 * Manipulação de arrays e objetos com caminho em notação de ponto.
 *
 *   ArrayHelper::get($dados, 'cliente.endereco.cidade', '-');
 *   ArrayHelper::set($dados, 'cliente.nome', 'Ana');
 *   ArrayHelper::pluck($pedidos, 'id');
 *   ArrayHelper::groupBy($pedidos, 'status');
 */

class ArrayHelper
{
    public static function toObject(
        array $dados,
        $recursivo = false,
        $preservarListas = true
    ) {
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
            }

            $resultado[$chave] = $registro;
        }

        return $resultado;
    }

    public static function toArray($dados, $recursivo = false)
    {
        if ($dados instanceof stdClass) {
            $dados = get_object_vars($dados);
        }

        if (!is_array($dados)) {
            throw new InvalidArgumentException(
                'Os dados devem ser um array ou stdClass.'
            );
        }

        if (!$recursivo) {
            return $dados;
        }

        foreach ($dados as $chave => $valor) {
            if (is_array($valor) || $valor instanceof stdClass) {
                $dados[$chave] = self::toArray($valor, true);
            }
        }

        return $dados;
    }

    public static function isList(array $dados)
    {
        $indice = 0;

        foreach ($dados as $chave => $valor) {
            if ($chave !== $indice) {
                return false;
            }

            $indice++;
        }

        return true;
    }

    public static function get($dados, $caminho, $padrao = null)
    {
        if ($caminho === null) {
            return $dados;
        }

        foreach (self::segments($caminho) as $segmento) {
            $encontrado = false;
            $dados = self::readKey($dados, $segmento, $encontrado);

            if (!$encontrado) {
                return $padrao;
            }
        }

        return $dados;
    }

    public static function has($dados, $caminho)
    {
        if ($caminho === null) {
            return false;
        }

        foreach (self::segments($caminho) as $segmento) {
            $encontrado = false;
            $dados = self::readKey($dados, $segmento, $encontrado);

            if (!$encontrado) {
                return false;
            }
        }

        return true;
    }

    public static function set(array &$dados, $caminho, $valor)
    {
        $segmentos = self::segments($caminho);
        $referencia = &$dados;

        foreach ($segmentos as $segmento) {
            if (
                !array_key_exists($segmento, $referencia)
                || !is_array($referencia[$segmento])
            ) {
                $referencia[$segmento] = [];
            }

            $referencia = &$referencia[$segmento];
        }

        $referencia = $valor;

        return $dados;
    }

    public static function forget(array &$dados, $caminho)
    {
        $segmentos = self::segments($caminho);
        $ultimaChave = array_pop($segmentos);
        $referencia = &$dados;

        foreach ($segmentos as $segmento) {
            if (
                !array_key_exists($segmento, $referencia)
                || !is_array($referencia[$segmento])
            ) {
                return $dados;
            }

            $referencia = &$referencia[$segmento];
        }

        unset($referencia[$ultimaChave]);

        return $dados;
    }

    public static function only(array $dados, array $chaves)
    {
        $resultado = [];

        foreach ($chaves as $chave) {
            if (array_key_exists($chave, $dados)) {
                $resultado[$chave] = $dados[$chave];
            }
        }

        return $resultado;
    }

    public static function except(array $dados, array $chaves)
    {
        foreach ($chaves as $chave) {
            unset($dados[$chave]);
        }

        return $dados;
    }

    public static function pluck(
        array $registros,
        $caminho,
        $chavePor = null
    ) {
        $resultado = [];

        foreach ($registros as $registro) {
            $valor = self::get($registro, $caminho);

            if ($chavePor === null) {
                $resultado[] = $valor;
                continue;
            }

            if (!self::has($registro, $chavePor)) {
                continue;
            }

            $chave = self::get($registro, $chavePor);

            self::validateIndex($chave);

            $resultado[$chave] = $valor;
        }

        return $resultado;
    }

    public static function keyBy(array $registros, $caminho)
    {
        $resultado = [];

        foreach ($registros as $registro) {
            if (!self::has($registro, $caminho)) {
                continue;
            }

            $chave = self::get($registro, $caminho);

            self::validateIndex($chave);

            $resultado[$chave] = $registro;
        }

        return $resultado;
    }

    public static function groupBy(array $registros, $caminho)
    {
        $resultado = [];

        foreach ($registros as $registro) {
            if (!self::has($registro, $caminho)) {
                continue;
            }

            $chave = self::get($registro, $caminho);

            self::validateIndex($chave);

            if (!array_key_exists($chave, $resultado)) {
                $resultado[$chave] = [];
            }

            $resultado[$chave][] = $registro;
        }

        return $resultado;
    }

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

        return $padrao;
    }

    public static function where(
        array $registros,
        $caminho,
        $valor,
        $preservarChaves = false
    ) {
        $resultado = [];

        foreach ($registros as $chave => $registro) {
            if (!self::has($registro, $caminho)) {
                continue;
            }

            if (self::get($registro, $caminho) !== $valor) {
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

    private static function readKey(
        $dados,
        $chave,
        &$encontrado
    ) {
        if (is_object($dados)) {
            $dados = get_object_vars($dados);
        }

        $encontrado = is_array($dados)
            && array_key_exists($chave, $dados);

        return $encontrado ? $dados[$chave] : null;
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
