import pathlib, zipfile, shutil, hashlib

src=pathlib.Path('kda/kimiya-diagnostic-agent-v1.1.7.zip')
work=pathlib.Path('/tmp/kda118')
if work.exists(): shutil.rmtree(work)
work.mkdir()
with zipfile.ZipFile(src,'r') as z: z.extractall(work)
root=work/'kimiya-diagnostic-agent'

main=root/'kimiya-diagnostic-agent.php'
s=main.read_text()
s=s.replace('Version: 1.1.7','Version: 1.1.8')
s=s.replace("define('KDA_VERSION', '1.1.7');","define('KDA_VERSION', '1.1.8');")
s=s.replace("require_once KDA_DIR . 'includes/class-kda-repair.php';\n",
            "require_once KDA_DIR . 'includes/class-kda-repair.php';\nrequire_once KDA_DIR . 'includes/class-kda-listing-optimizer.php';\n")
main.write_text(s)

shutil.copy2('kda/patches/class-kda-listing-optimizer.php', root/'includes/class-kda-listing-optimizer.php')

admin=root/'includes/class-kda-repair-admin.php'
a=admin.read_text()
a=a.replace("        add_action('admin_post_kda_rollback_jetmenu_repair', array(__CLASS__, 'rollback'));\n",
            "        add_action('admin_post_kda_rollback_jetmenu_repair', array(__CLASS__, 'rollback'));\n        add_action('admin_post_kda_enable_listing_lazy', array(__CLASS__, 'enable_listing_lazy'));\n        add_action('admin_post_kda_disable_listing_lazy', array(__CLASS__, 'disable_listing_lazy'));\n")

marker="""        echo '</form>';
        echo '</div>';
"""
insert="""        echo '</form>';

        echo '<hr style="margin:28px 0;max-width:760px">';
        echo '<h2>JetEngine Listing Grid Optimization</h2>';
        echo '<p>Targets only the known slow Listing Grid widgets on the homepage. No Elementor content is deleted or rewritten.</p>';
        echo '<table class="widefat striped" style="max-width:760px"><tbody>';
        echo '<tr><th style="width:260px">Listing Grid Lazy Load</th><td><code>' . (KDA_Listing_Optimizer::enabled() ? 'enabled' : 'disabled') . '</code></td></tr>';
        echo '<tr><th>Target widgets</th><td><code>10 homepage JetEngine grids</code></td></tr>';
        echo '</tbody></table><p></p>';
        echo '<form method="post" action="' . esc_url(admin_url('admin-post.php')) . '">';
        echo '<input type="hidden" name="action" value="kda_enable_listing_lazy">';
        wp_nonce_field('kda_enable_listing_lazy');
        submit_button('Enable Listing Grid Lazy Load', 'primary');
        echo '</form>';
        echo '<form method="post" action="' . esc_url(admin_url('admin-post.php')) . '">';
        echo '<input type="hidden" name="action" value="kda_disable_listing_lazy">';
        wp_nonce_field('kda_disable_listing_lazy');
        submit_button('Rollback Listing Grid Lazy Load', 'secondary');
        echo '</form>';

        echo '</div>';
"""
if marker not in a:
    raise SystemExit('admin page marker missing')
a=a.replace(marker,insert,1)

needle="""    public static function rollback() {
"""
methods="""    public static function enable_listing_lazy() {
        if (!current_user_can('manage_options')) { wp_die('Forbidden'); }
        check_admin_referer('kda_enable_listing_lazy');
        KDA_Listing_Optimizer::enable();
        set_transient('kda_repair_notice_' . get_current_user_id(), array('ok' => true, 'message' => 'Homepage JetEngine Listing Grid Lazy Load enabled. Rollback is available below.'), MINUTE_IN_SECONDS);
        wp_safe_redirect(admin_url('tools.php?page=kda-repair'));
        exit;
    }

    public static function disable_listing_lazy() {
        if (!current_user_can('manage_options')) { wp_die('Forbidden'); }
        check_admin_referer('kda_disable_listing_lazy');
        KDA_Listing_Optimizer::disable();
        set_transient('kda_repair_notice_' . get_current_user_id(), array('ok' => true, 'message' => 'Homepage JetEngine Listing Grid Lazy Load rolled back.'), MINUTE_IN_SECONDS);
        wp_safe_redirect(admin_url('tools.php?page=kda-repair'));
        exit;
    }

"""
if needle not in a:
    raise SystemExit('rollback marker missing')
a=a.replace(needle,methods+needle,1)
admin.write_text(a)

readme=root/'readme.txt'
r=readme.read_text().replace('Stable tag: 1.1.7','Stable tag: 1.1.8')
r += "\n= 1.1.8 =\n* Added reversible Lazy Load optimization for the known slow JetEngine Listing Grid widgets on the homepage.\n"
readme.write_text(r)

out=pathlib.Path('kda/kimiya-diagnostic-agent-v1.1.8.zip')
with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
    for f in sorted(root.rglob('*')):
        if f.is_file():
            arc=pathlib.Path('kimiya-diagnostic-agent')/f.relative_to(root)
            z.write(f,arc.as_posix())
sha=hashlib.sha256(out.read_bytes()).hexdigest()
pathlib.Path('kda/sha256-1.1.8.txt').write_text(sha+'\n')
print(out, out.stat().st_size, sha)
