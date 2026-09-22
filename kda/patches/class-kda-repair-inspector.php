<?php
if (!defined('ABSPATH')) { exit; }

final class KDA_Repair_Inspector {
    private static function find_element($nodes, $target) {
        if (!is_array($nodes)) { return null; }
        foreach ($nodes as $node) {
            if (!is_array($node)) { continue; }
            if (isset($node['id']) && (string)$node['id'] === (string)$target) {
                return $node;
            }
            if (!empty($node['elements']) && is_array($node['elements'])) {
                $found = self::find_element($node['elements'], $target);
                if ($found) { return $found; }
            }
        }
        return null;
    }

    public static function inspect() {
        global $wpdb;
        $ids = array('34f25e3','cb77a77','b0d4009','796b45b','16f984b','8bdd7f1','1f733a2','7e3f143','0579a12','ee0cf31','fa8dc76');
        $elements = array();
        foreach ($ids as $id) {
            $like = '%' . $wpdb->esc_like('"id":"' . $id . '"') . '%';
            $rows = $wpdb->get_results($wpdb->prepare(
                "SELECT pm.post_id, pm.meta_value, p.post_title, p.post_type, p.post_status
                 FROM {$wpdb->postmeta} pm
                 JOIN {$wpdb->posts} p ON p.ID = pm.post_id
                 WHERE pm.meta_key = '_elementor_data' AND pm.meta_value LIKE %s
                 ORDER BY pm.post_id DESC LIMIT 5",
                 $like
            ), ARRAY_A);
            $matches = array();
            foreach ((array)$rows as $row) {
                $data = json_decode((string)$row['meta_value'], true);
                if (!is_array($data)) { continue; }
                $node = self::find_element($data, $id);
                if (!$node) { continue; }
                $matches[] = array(
                    'post_id' => (int)$row['post_id'],
                    'post_title' => (string)$row['post_title'],
                    'post_type' => (string)$row['post_type'],
                    'post_status' => (string)$row['post_status'],
                    'element' => KDA_Security::redact(array(
                        'id' => isset($node['id']) ? $node['id'] : '',
                        'elType' => isset($node['elType']) ? $node['elType'] : '',
                        'widgetType' => isset($node['widgetType']) ? $node['widgetType'] : '',
                        'settings' => isset($node['settings']) ? $node['settings'] : array(),
                    )),
                );
            }
            $elements[$id] = $matches;
        }

        $option_rows = $wpdb->get_results(
            "SELECT option_name, option_value, autoload FROM {$wpdb->options}
             WHERE option_name LIKE '%jet%menu%' OR option_name LIKE '%jet_menu%' OR option_name LIKE '%jet-menu%'
             ORDER BY option_name ASC LIMIT 100",
            ARRAY_A
        );
        $options = array();
        foreach ((array)$option_rows as $row) {
            $val = maybe_unserialize($row['option_value']);
            $options[] = array(
                'option_name' => $row['option_name'],
                'autoload' => $row['autoload'],
                'value' => KDA_Security::redact($val),
            );
        }

        return array(
            'agent_version' => KDA_VERSION,
            'read_only' => true,
            'elements' => $elements,
            'jetmenu_related_options' => $options,
        );
    }
}
