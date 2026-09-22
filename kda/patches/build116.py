import pathlib, zipfile, shutil, hashlib

src=pathlib.Path('kda/kimiya-diagnostic-agent-v1.1.5.zip')
work=pathlib.Path('/tmp/kda116')
if work.exists():
    shutil.rmtree(work)
work.mkdir()
with zipfile.ZipFile(src,'r') as z:
    z.extractall(work)
root=work/'kimiya-diagnostic-agent'

main=root/'kimiya-diagnostic-agent.php'
s=main.read_text()
s=s.replace('Version: 1.1.5','Version: 1.1.6')
s=s.replace("define('KDA_VERSION', '1.1.5');","define('KDA_VERSION', '1.1.6');")
s=s.replace("require_once KDA_DIR . 'includes/class-kda-repair-inspector.php';\n",
            "require_once KDA_DIR . 'includes/class-kda-repair-inspector.php';\nrequire_once KDA_DIR . 'includes/class-kda-repair.php';\n")
marker="""        register_rest_route('kda/v1', '/signed-repair-inspect', array(
            'methods' => WP_REST_Server::READABLE,
            'callback' => array($this, 'rest_repair_inspect'),
            'permission_callback' => array('KDA_Security', 'authorize_signed_profile_request'),
        ));
"""
addition=marker+"""        register_rest_route('kda/v1', '/signed-repair', array(
            'methods' => WP_REST_Server::CREATABLE,
            'callback' => array($this, 'rest_repair'),
            'permission_callback' => array('KDA_Security', 'authorize_signed_repair_request'),
        ));
"""
if marker not in s:
    raise SystemExit('repair inspect route marker missing')
s=s.replace(marker,addition,1)
marker2="    public function rest_errors() {\n"
repl2="""    public function rest_repair($request) {
        $action = sanitize_key((string)$request->get_param('action'));
        if ($action === 'jetmenu_performance') {
            $result = KDA_Repair::apply_jetmenu();
        } elseif ($action === 'rollback_jetmenu') {
            $result = KDA_Repair::rollback_jetmenu();
        } else {
            return new WP_Error('kda_repair_unknown', 'Unknown repair action.', array('status' => 400));
        }
        if (is_wp_error($result)) { return $result; }
        return $this->no_store($result);
    }

"""+marker2
if marker2 not in s:
    raise SystemExit('rest_errors marker missing')
s=s.replace(marker2,repl2,1)
main.write_text(s)

sec=root/'includes/class-kda-security.php'
t=sec.read_text()
needle="    public static function authorize_signed_update_trigger($request) {\n"
method="""    public static function authorize_signed_repair_request($request) {
        if (!function_exists('sodium_crypto_sign_verify_detached')) {
            return new WP_Error('kda_no_sodium', 'PHP Sodium extension is required.', array('status' => 503));
        }
        $ts = (int)$request->get_param('ts');
        $nonce = trim((string)$request->get_param('nonce'));
        $sig_text = trim((string)$request->get_param('sig'));
        $action = sanitize_key((string)$request->get_param('action'));
        $allowed = array('jetmenu_performance','rollback_jetmenu');
        if (!$ts || abs(time() - $ts) > 600 || !in_array($action, $allowed, true) || !preg_match('/^[A-Za-z0-9_-]{20,128}$/', $nonce) || !$sig_text) {
            return new WP_Error('kda_signed_repair_invalid', 'Invalid or expired signed repair request.', array('status' => 403));
        }
        $nonce_key = 'kda_signed_repair_nonce_' . substr(hash('sha256', $nonce), 0, 32);
        if (get_transient($nonce_key)) {
            return new WP_Error('kda_signed_repair_replay', 'Signed repair request was already used.', array('status' => 409));
        }
        $pub = base64_decode((string)get_option('kda_update_public_key', KDA_DEFAULT_PUBLIC_KEY), true);
        $sig = self::decode_b64url($sig_text);
        if ($pub === false || strlen($pub) !== SODIUM_CRYPTO_SIGN_PUBLICKEYBYTES || $sig === false || strlen($sig) !== SODIUM_CRYPTO_SIGN_BYTES) {
            return new WP_Error('kda_signed_repair_signature_format', 'Invalid signature format.', array('status' => 403));
        }
        $payload = "repair\n" . $action . "\n" . $ts . "\n" . $nonce;
        if (!sodium_crypto_sign_verify_detached($sig, $payload, $pub)) {
            return new WP_Error('kda_signed_repair_signature', 'Invalid signed repair signature.', array('status' => 403));
        }
        set_transient($nonce_key, 1, 30 * MINUTE_IN_SECONDS);
        return true;
    }

"""
if needle not in t:
    raise SystemExit('update auth marker missing')
t=t.replace(needle,method+needle,1)
sec.write_text(t)

shutil.copy2('kda/patches/class-kda-repair.php', root/'includes/class-kda-repair.php')

readme=root/'readme.txt'
r=readme.read_text().replace('Stable tag: 1.1.5','Stable tag: 1.1.6')
r += "\n= 1.1.6 =\n* Added limited signed JetMenu performance repair with automatic rollback backup.\n"
readme.write_text(r)

out=pathlib.Path('kda/kimiya-diagnostic-agent-v1.1.6.zip')
with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
    for f in sorted(root.rglob('*')):
        if f.is_file():
            arc=pathlib.Path('kimiya-diagnostic-agent')/f.relative_to(root)
            z.write(f,arc.as_posix())
sha=hashlib.sha256(out.read_bytes()).hexdigest()
pathlib.Path('kda/sha256-1.1.6.txt').write_text(sha+'\n')
print(out, out.stat().st_size, sha)
