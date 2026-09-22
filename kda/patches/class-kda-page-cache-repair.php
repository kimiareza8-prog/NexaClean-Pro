<?php
if (!defined('ABSPATH')) { exit; }

final class KDA_Page_Cache_Repair {
    const BACKUP_OPTION = 'kda_page_cache_backup_111';
    const PLUGIN = 'litespeed-cache/litespeed-cache.php';

    public static function status() {
        if (!function_exists('is_plugin_active')) {
            require_once ABSPATH . 'wp-admin/includes/plugin.php';
        }
        $installed = file_exists(WP_PLUGIN_DIR . '/' . self::PLUGIN);
        $active = $installed && is_plugin_active(self::PLUGIN);
        $conf = get_option('litespeed-cache-conf', array());
        return array(
            'installed' => $installed,
            'active' => $active,
            'cache_setting' => is_array($conf) && isset($conf['cache']) ? $conf['cache'] : null,
            'advanced_cache_exists' => is_file(WP_CONTENT_DIR . '/advanced-cache.php'),
            'advanced_cache_bytes' => is_file(WP_CONTENT_DIR . '/advanced-cache.php') ? (int)@filesize(WP_CONTENT_DIR . '/advanced-cache.php') : 0,
        );
    }

    private static function backup_once() {
        if (get_option(self::BACKUP_OPTION, null) !== null) { return; }
        if (!function_exists('is_plugin_active')) {
            require_once ABSPATH . 'wp-admin/includes/plugin.php';
        }
        $path = WP_CONTENT_DIR . '/advanced-cache.php';
        $exists = is_file($path);
        $content = $exists && is_readable($path) ? @file_get_contents($path) : '';
        add_option(self::BACKUP_OPTION, array(
            'created_utc' => gmdate('c'),
            'plugin_installed' => file_exists(WP_PLUGIN_DIR . '/' . self::PLUGIN),
            'plugin_active' => function_exists('is_plugin_active') ? is_plugin_active(self::PLUGIN) : false,
            'advanced_cache_exists' => $exists,
            'advanced_cache_b64' => base64_encode(is_string($content) ? $content : ''),
            'litespeed_conf' => get_option('litespeed-cache-conf', null),
        ), '', false);
    }

    public static function install_enable() {
        if (!current_user_can('install_plugins') || !current_user_can('activate_plugins')) {
            return new WP_Error('kda_cache_permissions', 'Current user cannot install/activate plugins.');
        }
        self::backup_once();

        require_once ABSPATH . 'wp-admin/includes/plugin.php';

        if (!file_exists(WP_PLUGIN_DIR . '/' . self::PLUGIN)) {
            require_once ABSPATH . 'wp-admin/includes/plugin-install.php';
            require_once ABSPATH . 'wp-admin/includes/class-wp-upgrader.php';

            $api = plugins_api('plugin_information', array(
                'slug' => 'litespeed-cache',
                'fields' => array('sections' => false),
            ));
            if (is_wp_error($api) || empty($api->download_link)) {
                return is_wp_error($api) ? $api : new WP_Error('kda_cache_download', 'Could not resolve LiteSpeed Cache download URL.');
            }

            $skin = new Automatic_Upgrader_Skin();
            $upgrader = new Plugin_Upgrader($skin);
            $installed = $upgrader->install($api->download_link);
            if (is_wp_error($installed)) { return $installed; }
            if (!$installed || !file_exists(WP_PLUGIN_DIR . '/' . self::PLUGIN)) {
                return new WP_Error('kda_cache_install_failed', 'LiteSpeed Cache installation did not complete.');
            }
        }

        $activated = activate_plugin(self::PLUGIN, '', false, true);
        if (is_wp_error($activated)) { return $activated; }

        $conf = get_option('litespeed-cache-conf', array());
        if (!is_array($conf)) { $conf = array(); }
        $conf['cache'] = 1;
        update_option('litespeed-cache-conf', $conf);
        wp_cache_delete('litespeed-cache-conf', 'options');

        if (has_action('litespeed_purge_all')) {
            do_action('litespeed_purge_all');
        }

        return array('ok' => true, 'status' => self::status());
    }

    public static function rollback() {
        $backup = get_option(self::BACKUP_OPTION, null);
        if (!is_array($backup)) {
            return new WP_Error('kda_cache_no_backup', 'No page-cache repair backup exists.');
        }

        require_once ABSPATH . 'wp-admin/includes/plugin.php';

        if (empty($backup['plugin_active']) && is_plugin_active(self::PLUGIN)) {
            deactivate_plugins(self::PLUGIN, true, false);
        }

        if (array_key_exists('litespeed_conf', $backup)) {
            if ($backup['litespeed_conf'] === null) {
                delete_option('litespeed-cache-conf');
            } else {
                update_option('litespeed-cache-conf', $backup['litespeed_conf']);
            }
        }

        $path = WP_CONTENT_DIR . '/advanced-cache.php';
        if (!empty($backup['advanced_cache_exists'])) {
            $content = base64_decode((string)$backup['advanced_cache_b64'], true);
            if ($content === false) { $content = ''; }
            @file_put_contents($path, $content, LOCK_EX);
        } elseif (is_file($path)) {
            $raw = @file_get_contents($path);
            if (is_string($raw) && stripos($raw, 'litespeed') !== false) {
                @unlink($path);
            }
        }

        clearstatcache(true, $path);
        return array('ok' => true, 'status' => self::status());
    }
}
