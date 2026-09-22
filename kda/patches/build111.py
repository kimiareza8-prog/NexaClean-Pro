import pathlib, zipfile, shutil, hashlib

src=pathlib.Path('kda/kimiya-diagnostic-agent-v1.1.10.zip')
work=pathlib.Path('/tmp/kda111')
if work.exists(): shutil.rmtree(work)
work.mkdir()
with zipfile.ZipFile(src,'r') as z: z.extractall(work)
root=work/'kimiya-diagnostic-agent'

main=root/'kimiya-diagnostic-agent.php'
s=main.read_text()
s=s.replace('Version: 1.1.10','Version: 1.1.11')
s=s.replace("define('KDA_VERSION', '1.1.10');","define('KDA_VERSION', '1.1.11');")
s=s.replace("require_once KDA_DIR . 'includes/class-kda-cache-inspector.php';\n",
            "require_once KDA_DIR . 'includes/class-kda-cache-inspector.php';\nrequire_once KDA_DIR . 'includes/class-kda-page-cache-repair.php';\n")
main.write_text(s)

shutil.copy2('kda/patches/class-kda-page-cache-repair.php', root/'includes/class-kda-page-cache-repair.php')

admin=root/'includes/class-kda-repair-admin.php'
a=admin.read_text()
needle="        add_action('admin_post_kda_disable_listing_lazy', array(__CLASS__, 'disable_listing_lazy'));\n"
replacement=needle+"        add_action('admin_post_kda_enable_page_cache', array(__CLASS__, 'enable_page_cache'));\n        add_action('admin_post_kda_rollback_page_cache', array(__CLASS__, 'rollback_page_cache'));\n"
if needle not in a:
    raise SystemExit('admin init marker missing')
a=a.replace(needle,replacement,1)

marker="        echo '</div>';\n"
section="""        echo '<hr style="margin:28px 0;max-width:760px">';
        echo '<h2>Full Page Cache</h2>';
        echo '<p>The current advanced-cache.php is empty, so WordPress is rebuilding the homepage on each anonymous request. This repair installs/activates LiteSpeed Cache and enables only page cache first.</p>';
        $cache_status = KDA_Page_Cache_Repair::status();
        echo '<table class="widefat striped" style="max-width:760px"><tbody>';
        echo '<tr><th style="width:260px">LiteSpeed Cache installed</th><td><code>' . (!empty($cache_status['installed']) ? 'yes' : 'no') . '</code></td></tr>';
        echo '<tr><th>LiteSpeed Cache active</th><td><code>' . (!empty($cache_status['active']) ? 'yes' : 'no') . '</code></td></tr>';
        echo '<tr><th>Page cache setting</th><td><code>' . esc_html(var_export($cache_status['cache_setting'], true)) . '</code></td></tr>';
        echo '<tr><th>advanced-cache.php bytes</th><td><code>' . esc_html((string)$cache_status['advanced_cache_bytes']) . '</code></td></tr>';
        echo '</tbody></table><p></p>';
        echo '<form method="post" action="' . esc_url(admin_url('admin-post.php')) . '">';
        echo '<input type="hidden" name="action" value="kda_enable_page_cache">';
        wp_nonce_field('kda_enable_page_cache');
        submit_button('Install / Enable LiteSpeed Page Cache', 'primary');
        echo '</form>';
        echo '<form method="post" action="' . esc_url(admin_url('admin-post.php')) . '">';
        echo '<input type="hidden" name="action" value="kda_rollback_page_cache">';
        wp_nonce_field('kda_rollback_page_cache');
        submit_button('Rollback Page Cache Repair', 'secondary');
        echo '</form>';

"""+marker
if marker not in a:
    raise SystemExit('admin closing marker missing')
# replace last closing marker only
idx=a.rfind(marker)
a=a[:idx]+section+a[idx+len(marker):]

method_marker="    public static function rollback() {\n"
methods="""    public static function enable_page_cache() {
        if (!current_user_can('manage_options')) { wp_die('Forbidden'); }
        check_admin_referer('kda_enable_page_cache');
        $result = KDA_Page_Cache_Repair::install_enable();
        if (is_wp_error($result)) {
            $notice = array('ok' => false, 'message' => 'Page cache repair failed: ' . $result->get_error_message());
        } else {
            $notice = array('ok' => true, 'message' => 'LiteSpeed Page Cache install/enable completed. The cache will now be tested externally.');
        }
        set_transient('kda_repair_notice_' . get_current_user_id(), $notice, MINUTE_IN_SECONDS);
        wp_safe_redirect(admin_url('tools.php?page=kda-repair'));
        exit;
    }

    public static function rollback_page_cache() {
        if (!current_user_can('manage_options')) { wp_die('Forbidden'); }
        check_admin_referer('kda_rollback_page_cache');
        $result = KDA_Page_Cache_Repair::rollback();
        if (is_wp_error($result)) {
            $notice = array('ok' => false, 'message' => 'Page cache rollback failed: ' . $result->get_error_message());
        } else {
            $notice = array('ok' => true, 'message' => 'Page cache repair rolled back.');
        }
        set_transient('kda_repair_notice_' . get_current_user_id(), $notice, MINUTE_IN_SECONDS);
        wp_safe_redirect(admin_url('tools.php?page=kda-repair'));
        exit;
    }

"""
if method_marker not in a:
    raise SystemExit('rollback method marker missing')
a=a.replace(method_marker,methods+method_marker,1)
admin.write_text(a)

readme=root/'readme.txt'
r=readme.read_text().replace('Stable tag: 1.1.10','Stable tag: 1.1.11')
r += "\n= 1.1.11 =\n* Added reversible LiteSpeed page-cache installation/enable repair from Tools > KDA Repair.\n"
readme.write_text(r)

out=pathlib.Path('kda/kimiya-diagnostic-agent-v1.1.11.zip')
with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
    for f in sorted(root.rglob('*')):
        if f.is_file():
            arc=pathlib.Path('kimiya-diagnostic-agent')/f.relative_to(root)
            z.write(f,arc.as_posix())
sha=hashlib.sha256(out.read_bytes()).hexdigest()
pathlib.Path('kda/sha256-1.1.11.txt').write_text(sha+'\n')
print(out, out.stat().st_size, sha)
