import pathlib, zipfile, shutil, hashlib

src=pathlib.Path('kda/kimiya-diagnostic-agent-v1.1.3.zip')
work=pathlib.Path('/tmp/kda114')
if work.exists():
    shutil.rmtree(work)
work.mkdir()
with zipfile.ZipFile(src,'r') as z:
    z.extractall(work)
root=work/'kimiya-diagnostic-agent'

main=root/'kimiya-diagnostic-agent.php'
s=main.read_text().replace('Version: 1.1.3','Version: 1.1.4').replace("define('KDA_VERSION', '1.1.3');","define('KDA_VERSION', '1.1.4');")
main.write_text(s)

p=root/'includes/class-kda-profiler.php'
s=p.read_text()
s=s.replace("    private static $http_calls = array();\n", "    private static $http_calls = array();\n    private static $element_starts = array();\n    private static $element_times = array();\n")
old="""        global $wpdb;
        if (isset($wpdb) && is_object($wpdb)) { $wpdb->save_queries = true; }

        $hooks = array('plugins_loaded','setup_theme','after_setup_theme','init','wp_loaded','template_redirect','wp_head','loop_start','wp_footer');
        foreach ($hooks as $hook) {
            add_action($hook, function() use ($hook) { KDA_Profiler::mark($hook); }, PHP_INT_MAX);
        }
"""
new="""        if (!defined('SAVEQUERIES')) { define('SAVEQUERIES', true); }

        $hooks = array('plugins_loaded','setup_theme','after_setup_theme','init','wp_loaded','template_redirect');
        foreach ($hooks as $hook) {
            add_action($hook, function() use ($hook) { KDA_Profiler::mark($hook); }, PHP_INT_MAX);
        }
        add_filter('template_include', array(__CLASS__, 'template_include'), PHP_INT_MAX);
        foreach (array('wp_head','loop_start','wp_footer') as $hook) {
            add_action($hook, function() use ($hook) { KDA_Profiler::mark($hook . '_start'); }, PHP_INT_MIN);
            add_action($hook, function() use ($hook) { KDA_Profiler::mark($hook . '_end'); }, PHP_INT_MAX);
        }
        add_action('get_header', function(){ KDA_Profiler::mark('get_header'); }, PHP_INT_MIN);
        add_action('get_footer', function(){ KDA_Profiler::mark('get_footer'); }, PHP_INT_MIN);
        add_action('elementor/frontend/before_render', array(__CLASS__, 'elementor_before'), PHP_INT_MIN, 1);
        add_action('elementor/frontend/after_render', array(__CLASS__, 'elementor_after'), PHP_INT_MAX, 1);
"""
if old not in s:
    raise SystemExit('profiler hook block missing')
s=s.replace(old,new,1)
needle="    public static function http_start($preempt, $args, $url) {\n"
methods="""    public static function template_include($template) {
        self::mark('template_include');
        return $template;
    }

    public static function elementor_before($element) {
        if (!self::$active || !is_object($element)) { return; }
        $id = method_exists($element, 'get_id') ? (string)$element->get_id() : spl_object_hash($element);
        self::$element_starts[$id] = microtime(true);
    }

    public static function elementor_after($element) {
        if (!self::$active || !is_object($element)) { return; }
        $id = method_exists($element, 'get_id') ? (string)$element->get_id() : spl_object_hash($element);
        if (!isset(self::$element_starts[$id])) { return; }
        $ms = round((microtime(true) - self::$element_starts[$id]) * 1000, 1);
        unset(self::$element_starts[$id]);
        $name = method_exists($element, 'get_name') ? (string)$element->get_name() : get_class($element);
        $type = method_exists($element, 'get_type') ? (string)$element->get_type() : '';
        self::$element_times[] = array('id' => $id, 'name' => $name, 'type' => $type, 'ms' => $ms);
    }

"""
if needle not in s:
    raise SystemExit('http_start marker missing')
s=s.replace(needle,methods+needle,1)
old2="        usort(self::$http_calls, function($a,$b){ return ($b['ms'] ?: 0) <=> ($a['ms'] ?: 0); });\n        $data = array(\n"
new2="        usort(self::$http_calls, function($a,$b){ return ($b['ms'] ?: 0) <=> ($a['ms'] ?: 0); });\n        usort(self::$element_times, function($a,$b){ return $b['ms'] <=> $a['ms']; });\n        $data = array(\n"
if old2 not in s:
    raise SystemExit('finish sort marker missing')
s=s.replace(old2,new2,1)
old3="            'http_call_count' => count(self::$http_calls),\n"
new3="            'http_call_count' => count(self::$http_calls),\n            'elementor_top_elements' => array_slice(self::$element_times, 0, 30),\n            'elementor_element_count' => count(self::$element_times),\n"
if old3 not in s:
    raise SystemExit('http count marker missing')
s=s.replace(old3,new3,1)
p.write_text(s)

readme=root/'readme.txt'
r=readme.read_text().replace('Stable tag: 1.1.3','Stable tag: 1.1.4')
r += "\n= 1.1.4 =\n* Profiler now captures SQL timings and Elementor element render timings.\n"
readme.write_text(r)

out=pathlib.Path('kda/kimiya-diagnostic-agent-v1.1.4.zip')
with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
    for f in sorted(root.rglob('*')):
        if f.is_file():
            arc=pathlib.Path('kimiya-diagnostic-agent')/f.relative_to(root)
            z.write(f,arc.as_posix())
sha=hashlib.sha256(out.read_bytes()).hexdigest()
pathlib.Path('kda/sha256-1.1.4.txt').write_text(sha+'\n')
print(out, out.stat().st_size, sha)
