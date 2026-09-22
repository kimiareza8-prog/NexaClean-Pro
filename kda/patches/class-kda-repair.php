<?php
if (!defined('ABSPATH')) { exit; }

final class KDA_Repair {
    const BACKUP_OPTION = 'kda_repair_backup_jetmenu_116';

    public static function apply_jetmenu() {
        $opts = get_option('jet_menu_options', array());
        if (!is_array($opts)) {
            return new WP_Error('kda_repair_jetmenu_missing', 'JetMenu options are not an array.', array('status' => 500));
        }

        $before = array(
            'jet-menu-cache-css' => isset($opts['jet-menu-cache-css']) ? $opts['jet-menu-cache-css'] : null,
            'use-template-cache' => isset($opts['use-template-cache']) ? $opts['use-template-cache'] : null,
            'jet-menu-mega-ajax-loading' => isset($opts['jet-menu-mega-ajax-loading']) ? $opts['jet-menu-mega-ajax-loading'] : null,
        );

        if (get_option(self::BACKUP_OPTION, null) === null) {
            add_option(self::BACKUP_OPTION, array(
                'created_utc' => gmdate('c'),
                'agent_version' => KDA_VERSION,
                'jet_menu_options' => $opts,
            ), '', false);
        }

        $opts['jet-menu-cache-css'] = 'true';
        $opts['use-template-cache'] = 'true';
        $opts['jet-menu-mega-ajax-loading'] = 'true';

        $ok = update_option('jet_menu_options', $opts);
        wp_cache_delete('jet_menu_options', 'options');

        $after_opts = get_option('jet_menu_options', array());
        $after = array(
            'jet-menu-cache-css' => isset($after_opts['jet-menu-cache-css']) ? $after_opts['jet-menu-cache-css'] : null,
            'use-template-cache' => isset($after_opts['use-template-cache']) ? $after_opts['use-template-cache'] : null,
            'jet-menu-mega-ajax-loading' => isset($after_opts['jet-menu-mega-ajax-loading']) ? $after_opts['jet-menu-mega-ajax-loading'] : null,
        );

        return array(
            'ok' => true,
            'operation' => 'jetmenu_performance',
            'changed' => (bool)$ok,
            'backup_option' => self::BACKUP_OPTION,
            'before' => $before,
            'after' => $after,
        );
    }

    public static function rollback_jetmenu() {
        $backup = get_option(self::BACKUP_OPTION, null);
        if (!is_array($backup) || !isset($backup['jet_menu_options']) || !is_array($backup['jet_menu_options'])) {
            return new WP_Error('kda_repair_no_backup', 'No JetMenu repair backup exists.', array('status' => 404));
        }
        update_option('jet_menu_options', $backup['jet_menu_options']);
        wp_cache_delete('jet_menu_options', 'options');
        return array('ok' => true, 'operation' => 'rollback_jetmenu', 'restored' => true);
    }
}
