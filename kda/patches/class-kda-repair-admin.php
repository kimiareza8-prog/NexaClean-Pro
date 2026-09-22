<?php
if (!defined('ABSPATH')) { exit; }

final class KDA_Repair_Admin {
    public static function init() {
        add_action('admin_menu', array(__CLASS__, 'menu'));
        add_action('admin_post_kda_apply_jetmenu_repair', array(__CLASS__, 'apply'));
        add_action('admin_post_kda_rollback_jetmenu_repair', array(__CLASS__, 'rollback'));
    }

    public static function menu() {
        add_management_page(
            'KDA Repair',
            'KDA Repair',
            'manage_options',
            'kda-repair',
            array(__CLASS__, 'page')
        );
    }

    private static function current_values() {
        $opts = get_option('jet_menu_options', array());
        return array(
            'Mega Content Ajax Loading' => isset($opts['jet-menu-mega-ajax-loading']) ? $opts['jet-menu-mega-ajax-loading'] : '(missing)',
            'Cache menu CSS' => isset($opts['jet-menu-cache-css']) ? $opts['jet-menu-cache-css'] : '(missing)',
            'Template Content Cache' => isset($opts['use-template-cache']) ? $opts['use-template-cache'] : '(missing)',
        );
    }

    public static function page() {
        if (!current_user_can('manage_options')) { wp_die('Forbidden'); }
        $notice = get_transient('kda_repair_notice_' . get_current_user_id());
        delete_transient('kda_repair_notice_' . get_current_user_id());
        $values = self::current_values();
        echo '<div class="wrap"><h1>KDA Repair</h1>';
        echo '<p>This repair changes only JetMenu performance settings. A rollback backup is created automatically.</p>';
        if (is_array($notice)) {
            $class = !empty($notice['ok']) ? 'notice notice-success' : 'notice notice-error';
            echo '<div class="' . esc_attr($class) . '"><p>' . esc_html($notice['message']) . '</p></div>';
        }
        echo '<table class="widefat striped" style="max-width:760px"><tbody>';
        foreach ($values as $label => $value) {
            echo '<tr><th style="width:260px">' . esc_html($label) . '</th><td><code>' . esc_html((string)$value) . '</code></td></tr>';
        }
        echo '</tbody></table><p></p>';
        echo '<form method="post" action="' . esc_url(admin_url('admin-post.php')) . '">';
        echo '<input type="hidden" name="action" value="kda_apply_jetmenu_repair">';
        wp_nonce_field('kda_apply_jetmenu_repair');
        submit_button('Apply JetMenu Performance Repair', 'primary');
        echo '</form>';
        echo '<form method="post" action="' . esc_url(admin_url('admin-post.php')) . '">';
        echo '<input type="hidden" name="action" value="kda_rollback_jetmenu_repair">';
        wp_nonce_field('kda_rollback_jetmenu_repair');
        submit_button('Rollback JetMenu Repair', 'secondary');
        echo '</form>';
        echo '</div>';
    }

    public static function apply() {
        if (!current_user_can('manage_options')) { wp_die('Forbidden'); }
        check_admin_referer('kda_apply_jetmenu_repair');
        $result = KDA_Repair::apply_jetmenu();
        if (is_wp_error($result)) {
            $notice = array('ok' => false, 'message' => $result->get_error_message());
        } else {
            $notice = array('ok' => true, 'message' => 'JetMenu repair applied. Backup saved for rollback.');
        }
        set_transient('kda_repair_notice_' . get_current_user_id(), $notice, MINUTE_IN_SECONDS);
        wp_safe_redirect(admin_url('tools.php?page=kda-repair'));
        exit;
    }

    public static function rollback() {
        if (!current_user_can('manage_options')) { wp_die('Forbidden'); }
        check_admin_referer('kda_rollback_jetmenu_repair');
        $result = KDA_Repair::rollback_jetmenu();
        if (is_wp_error($result)) {
            $notice = array('ok' => false, 'message' => $result->get_error_message());
        } else {
            $notice = array('ok' => true, 'message' => 'JetMenu settings restored from backup.');
        }
        set_transient('kda_repair_notice_' . get_current_user_id(), $notice, MINUTE_IN_SECONDS);
        wp_safe_redirect(admin_url('tools.php?page=kda-repair'));
        exit;
    }
}

KDA_Repair_Admin::init();
