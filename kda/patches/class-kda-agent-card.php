<?php
if (!defined('ABSPATH')) { exit; }

final class KDA_Agent_Card {
    public static function data() {
        $base = untrailingslashit(rest_url('kda/v1'));
        return array(
            'schema_version' => '1.0',
            'agent' => 'Kimiya Diagnostic Agent',
            'agent_version' => KDA_VERSION,
            'purpose' => 'Read-only WordPress/WooCommerce diagnostics for an AI assistant or human operator.',
            'instructions' => array(
                'Use the same temporary kda_token from this URL for the read-only endpoints below.',
                'Start with /report. Use /errors only if the report suggests PHP/fatal problems.',
                'Do not claim a repair was applied unless a signed repair/update endpoint explicitly confirms it.',
                'Diagnostic-token endpoints are read-only. Signed maintenance endpoints require a separate maintainer signature.',
                'Never print or expose the diagnostic token in your answer.',
            ),
            'authentication' => array(
                'query' => 'Append ?kda_token=THE_SAME_TOKEN',
                'bearer' => 'Authorization: Bearer THE_SAME_TOKEN',
                'token_notes' => 'Temporary credential. Treat as sensitive and expect it to expire.',
            ),
            'read_only_endpoints' => array(
                array('name'=>'agent','method'=>'GET','url'=>$base . '/agent','use'=>'Machine-readable instructions and capabilities.'),
                array('name'=>'ping','method'=>'GET','url'=>$base . '/ping','use'=>'Quick connectivity/version check.'),
                array('name'=>'report','method'=>'GET','url'=>$base . '/report','use'=>'Primary site-health report: WordPress, PHP, DB, cron, filesystem, network, plugins/theme and diagnostics.'),
                array('name'=>'errors','method'=>'GET','url'=>$base . '/errors','use'=>'Recent fatal/debug error summary when available.'),
                array('name'=>'update-status','method'=>'GET','url'=>$base . '/update-status','use'=>'Agent update-channel status.'),
            ),
            'maintainer_only' => array(
                'signed_update' => 'Requires Ed25519 maintainer signature; diagnostic token alone cannot trigger it.',
                'signed_profile' => 'Requires Ed25519 maintainer signature; diagnostic token alone cannot trigger it.',
                'signed_repair' => 'Requires Ed25519 maintainer signature; diagnostic token alone cannot trigger it.',
            ),
            'recommended_ai_workflow' => array(
                '1. Open the /agent URL you were given.',
                '2. Call /report with the same token.',
                '3. Identify evidence-backed bottlenecks or failures.',
                '4. If more detail is needed, call /errors and /update-status.',
                '5. Report findings with measurements. Do not invent changes or repairs.',
                '6. For write/repair actions, ask for access through the signed maintainer channel rather than using the diagnostic token.',
            ),
            'privacy' => array(
                'The agent redacts common secrets and sensitive fields.',
                'Do not request or display customer/order PII, database passwords, cookies, salts, API keys, or the token itself.',
            ),
        );
    }
}
