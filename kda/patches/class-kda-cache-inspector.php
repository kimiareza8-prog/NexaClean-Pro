<?php
if (!defined('ABSPATH')) { exit; }

final class KDA_Cache_Inspector {
    private static function classify_file($path) {
        if (!is_file($path) || !is_readable($path)) {
            return array('exists'=>false);
        }
        $raw = @file_get_contents($path, false, null, 0, 65536);
        $raw = is_string($raw) ? $raw : '';
        $kind = 'unknown';
        $needles = array(
            'litespeed' => 'LiteSpeed Cache',
            'wp-super-cache' => 'WP Super Cache',
            'w3-total-cache' => 'W3 Total Cache',
            'wp-optimize' => 'WP-Optimize',
            'autoptimize' => 'Autoptimize',
        );
        $lower = strtolower($raw);
        foreach ($needles as $needle => $label) {
            if (strpos($lower, $needle) !== false) { $kind = $label; break; }
        }
        return array(
            'exists' => true,
            'bytes' => (int)@filesize($path),
            'sha256' => @hash_file('sha256', $path),
            'classified_as' => $kind,
        );
    }

    public static function inspect() {
        if (!function_exists('get_dropins')) {
            require_once ABSPATH . 'wp-admin/includes/plugin.php';
        }
        $dropins = function_exists('get_dropins') ? get_dropins() : array();
        $safe = array();
        foreach ((array)$dropins as $file => $data) {
            $safe[$file] = array(
                'name' => isset($data['Name']) ? (string)$data['Name'] : '',
                'description' => isset($data['Description']) ? wp_strip_all_tags((string)$data['Description']) : '',
            );
        }
        return array(
            'wp_cache_constant' => defined('WP_CACHE') ? (bool)WP_CACHE : null,
            'advanced_cache' => self::classify_file(WP_CONTENT_DIR . '/advanced-cache.php'),
            'object_cache' => self::classify_file(WP_CONTENT_DIR . '/object-cache.php'),
            'dropins' => $safe,
            'litespeed_runtime' => array(
                'php_sapi' => PHP_SAPI,
                'server_software' => isset($_SERVER['SERVER_SOFTWARE']) ? (string)$_SERVER['SERVER_SOFTWARE'] : '',
                'lscwp_loaded' => defined('LSCWP_V'),
                'litespeed_header_function' => function_exists('header_register_callback'),
            ),
        );
    }
}
