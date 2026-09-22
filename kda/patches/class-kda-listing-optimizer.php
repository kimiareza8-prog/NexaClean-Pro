<?php
if (!defined('ABSPATH')) { exit; }

final class KDA_Listing_Optimizer {
    const OPTION = 'kda_listing_lazy_enabled';

    public static function init() {
        add_action('elementor/frontend/widget/before_render', array(__CLASS__, 'before_render'), 1);
    }

    public static function enabled() {
        return (bool)get_option(self::OPTION, false);
    }

    public static function enable() {
        update_option(self::OPTION, 1, false);
        return true;
    }

    public static function disable() {
        update_option(self::OPTION, 0, false);
        return true;
    }

    public static function before_render($widget) {
        if (!self::enabled() || !is_front_page() || !is_object($widget)) { return; }
        if (!method_exists($widget, 'get_id') || !method_exists($widget, 'get_name') || !method_exists($widget, 'set_settings')) { return; }

        $id = (string)$widget->get_id();
        $name = (string)$widget->get_name();
        if ($name !== 'jet-listing-grid') { return; }

        $targets = array(
            'cb77a77','b0d4009','796b45b','16f984b','8bdd7f1',
            '1f733a2','7e3f143','0579a12','ee0cf31','fa8dc76'
        );
        if (!in_array($id, $targets, true)) { return; }

        $widget->set_settings('lazy_load', 'yes');
        $widget->set_settings('lazy_load_offset', '300');
    }
}

KDA_Listing_Optimizer::init();
