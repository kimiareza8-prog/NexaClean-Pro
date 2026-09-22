<?php
if (!defined('ABSPATH')) { exit; }

final class KDA_Profiler {
    private static $active = false;
    private static $key = '';
    private static $start = 0.0;
    private static $marks = array();
    private static $http_starts = array();
    private static $http_calls = array();

    public static function bootstrap() {
        if (empty($_GET['kda_profile_probe'])) { return; }
        $key = sanitize_text_field(wp_unslash((string)$_GET['kda_profile_probe']));
        if (!preg_match('/^[A-Za-z0-9_-]{32,128}$/', $key)) { return; }
        $expected = get_transient('kda_profile_probe_' . substr(hash('sha256', $key), 0, 32));
        if (!$expected || !hash_equals((string)$expected, hash('sha256', $key))) { return; }
        delete_transient('kda_profile_probe_' . substr(hash('sha256', $key), 0, 32));

        self::$active = true;
        self::$key = $key;
        self::$start = isset($GLOBALS['timestart']) ? (float)$GLOBALS['timestart'] : microtime(true);
        self::mark('kda_plugin_loaded');

        global $wpdb;
        if (isset($wpdb) && is_object($wpdb)) { $wpdb->save_queries = true; }

        $hooks = array('plugins_loaded','setup_theme','after_setup_theme','init','wp_loaded','template_redirect','wp_head','loop_start','wp_footer');
        foreach ($hooks as $hook) {
            add_action($hook, function() use ($hook) { KDA_Profiler::mark($hook); }, PHP_INT_MAX);
        }
        add_filter('pre_http_request', array(__CLASS__, 'http_start'), PHP_INT_MAX, 3);
        add_action('http_api_debug', array(__CLASS__, 'http_end'), PHP_INT_MAX, 5);
        add_action('shutdown', array(__CLASS__, 'finish'), PHP_INT_MAX);
    }

    public static function mark($name) {
        if (!self::$active) { return; }
        self::$marks[$name] = round((microtime(true) - self::$start) * 1000, 1);
    }

    public static function http_start($preempt, $args, $url) {
        if (!self::$active) { return $preempt; }
        $id = hash('sha256', (string)$url);
        if (!isset(self::$http_starts[$id])) { self::$http_starts[$id] = array(); }
        self::$http_starts[$id][] = microtime(true);
        return $preempt;
    }

    public static function http_end($response, $context, $class, $args, $url) {
        if (!self::$active) { return; }
        $id = hash('sha256', (string)$url);
        $started = null;
        if (!empty(self::$http_starts[$id])) { $started = array_shift(self::$http_starts[$id]); }
        $ms = $started ? round((microtime(true) - $started) * 1000, 1) : null;
        $parts = wp_parse_url((string)$url);
        $host = isset($parts['host']) ? strtolower($parts['host']) : '';
        $path = isset($parts['path']) ? $parts['path'] : '/';
        if (strlen($path) > 120) { $path = substr($path, 0, 120) . '…'; }
        $code = is_wp_error($response) ? 0 : (int)wp_remote_retrieve_response_code($response);
        self::$http_calls[] = array(
            'host' => $host,
            'path' => $path,
            'ms' => $ms,
            'http_code' => $code,
            'error' => is_wp_error($response) ? KDA_Security::redact($response->get_error_message()) : '',
        );
    }

    private static function sql_shape($sql) {
        $sql = preg_replace("/'(?:''|[^'])*'/", "?", (string)$sql);
        $sql = preg_replace('/\b\d+(?:\.\d+)?\b/', '?', $sql);
        $sql = preg_replace('/\s+/', ' ', trim($sql));
        if (strlen($sql) > 220) { $sql = substr($sql, 0, 220) . '…'; }
        return $sql;
    }

    private static function query_stats() {
        global $wpdb;
        $queries = (isset($wpdb->queries) && is_array($wpdb->queries)) ? $wpdb->queries : array();
        $total = 0.0; $top = array();
        foreach ($queries as $q) {
            $sql = isset($q[0]) ? $q[0] : '';
            $sec = isset($q[1]) ? (float)$q[1] : 0.0;
            $caller = isset($q[2]) ? (string)$q[2] : '';
            $total += $sec;
            $top[] = array('ms' => round($sec * 1000, 1), 'sql' => self::sql_shape($sql), 'caller' => KDA_Security::redact($caller));
        }
        usort($top, function($a,$b){ return $b['ms'] <=> $a['ms']; });
        $top = array_slice($top, 0, 20);
        return array('count' => count($queries), 'total_ms' => round($total * 1000, 1), 'top' => $top);
    }

    private static function phase_deltas() {
        $out = array(); $prev_name = 'wordpress_start'; $prev_ms = 0.0;
        foreach (self::$marks as $name => $ms) {
            $out[] = array('from' => $prev_name, 'to' => $name, 'ms' => round($ms - $prev_ms, 1));
            $prev_name = $name; $prev_ms = $ms;
        }
        $now = round((microtime(true) - self::$start) * 1000, 1);
        $out[] = array('from' => $prev_name, 'to' => 'shutdown', 'ms' => round($now - $prev_ms, 1));
        return $out;
    }

    public static function finish() {
        if (!self::$active || !self::$key) { return; }
        self::mark('shutdown_enter');
        $elapsed = round((microtime(true) - self::$start) * 1000, 1);
        usort(self::$http_calls, function($a,$b){ return ($b['ms'] ?: 0) <=> ($a['ms'] ?: 0); });
        $data = array(
            'total_php_ms' => $elapsed,
            'peak_memory_mb' => round(memory_get_peak_usage(true) / 1048576, 1),
            'marks_ms_from_wp_start' => self::$marks,
            'phase_deltas' => self::phase_deltas(),
            'database' => self::query_stats(),
            'http_calls' => array_slice(self::$http_calls, 0, 30),
            'http_total_ms' => round(array_sum(array_map(function($x){ return (float)($x['ms'] ?: 0); }, self::$http_calls)), 1),
            'http_call_count' => count(self::$http_calls),
        );
        set_transient('kda_profile_result_' . substr(hash('sha256', self::$key), 0, 32), $data, 5 * MINUTE_IN_SECONDS);
    }

    public static function run_home_profile() {
        $key = rtrim(strtr(base64_encode(random_bytes(32)), '+/', '-_'), '=');
        $hash = substr(hash('sha256', $key), 0, 32);
        set_transient('kda_profile_probe_' . $hash, hash('sha256', $key), 2 * MINUTE_IN_SECONDS);
        $url = add_query_arg(array('kda_profile_probe' => rawurlencode($key), '_kda_nocache' => time()), home_url('/'));
        $start = microtime(true);
        $r = wp_remote_get($url, array(
            'timeout' => 45,
            'redirection' => 2,
            'sslverify' => true,
            'headers' => array('Cache-Control' => 'no-cache', 'Pragma' => 'no-cache', 'Accept' => 'text/html'),
            'user-agent' => 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/153.0 Safari/537.36 KDA-Profiler/' . KDA_VERSION,
        ));
        $request_ms = round((microtime(true) - $start) * 1000, 1);
        $profile = get_transient('kda_profile_result_' . $hash);
        delete_transient('kda_profile_result_' . $hash);
        delete_transient('kda_profile_probe_' . $hash);
        return array(
            'probe_request_ms' => $request_ms,
            'probe_http_code' => is_wp_error($r) ? 0 : (int)wp_remote_retrieve_response_code($r),
            'probe_error' => is_wp_error($r) ? KDA_Security::redact($r->get_error_message()) : '',
            'profile' => is_array($profile) ? $profile : null,
        );
    }
}
