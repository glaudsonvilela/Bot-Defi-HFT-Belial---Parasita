//! FIX4 token-program proofs. Read-only metadata/invariant extension subset.
//! Not unrestricted Token-2022, execution approval or token risk certification.
use super::*;
pub const TOKEN: &str = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";
pub const TOKEN_2022: &str = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb";
pub const REVISION: &str = "FIX4_TOKEN_CAPABILITIES_V1";

pub fn supported(program: &str) -> bool {
    [TOKEN, TOKEN_2022].contains(&program)
}
pub fn declared(program: &str) -> &str {
    if program.is_empty() { TOKEN } else { program }
}
fn key(raw: &[u8], pos: usize) -> Result<String, String> {
    Ok(bs58::encode(raw.get(pos..pos + 32).ok_or("TOKEN_SHORT_KEY")?).into_string())
}
fn uint32(raw: &[u8], pos: usize) -> Result<u32, String> {
    let bytes: [u8; 4] = raw
        .get(pos..pos + 4)
        .ok_or("TOKEN_SHORT_U32")?
        .try_into()
        .map_err(|_| "TOKEN_U32")?;
    Ok(u32::from_le_bytes(bytes))
}
fn option(raw: &[u8], pos: usize) -> Result<(), String> {
    match uint32(raw, pos)? {
        0 | 1 => Ok(()),
        _ => Err("TOKEN_COPTION_INVALID".into()),
    }
}
fn metadata(raw: &[u8], mint: &str) -> Result<(), String> {
    if raw.len() < 80 || key(raw, 32)? != mint {
        return Err("TOKEN_METADATA_MINT_OR_LAYOUT".into());
    }
    fn string(raw: &[u8], pos: &mut usize) -> Result<(), String> {
        let size = uint32(raw, *pos)? as usize;
        *pos += 4;
        let end = (*pos).checked_add(size).ok_or("TOKEN_METADATA_OVERFLOW")?;
        std::str::from_utf8(raw.get(*pos..end).ok_or("TOKEN_METADATA_STRING_LENGTH")?)
            .map_err(|_| "TOKEN_METADATA_UTF8")?;
        *pos = end;
        Ok(())
    }
    let mut pos = 64;
    for _ in 0..3 {
        string(raw, &mut pos)?;
    }
    let pairs = uint32(raw, pos)? as usize;
    pos += 4;
    if pairs > (raw.len() - pos) / 8 {
        return Err("TOKEN_METADATA_PAIR_COUNT".into());
    }
    for _ in 0..pairs {
        string(raw, &mut pos)?;
        string(raw, &mut pos)?;
    }
    if raw[pos..].iter().any(|b| *b != 0) {
        return Err("TOKEN_METADATA_TRAILING_DATA".into());
    }
    Ok(())
}
fn extensions(raw: &[u8], program: &str, kind: u8, mint: &str) -> Result<(), String> {
    let base = if kind == 1 { 82 } else { 165 };
    if raw.len() < base || raw.len() == 355 || raw.len() > 1024 * 1024 {
        return Err("TOKEN_LAYOUT_OR_MULTISIG".into());
    }
    if program == TOKEN {
        return if raw.len() == base {
            Ok(())
        } else {
            Err("LEGACY_TOKEN_UNEXPECTED_EXTENSION_BYTES".into())
        };
    }
    if program != TOKEN_2022 {
        return Err("TOKEN_PROGRAM_UNSUPPORTED".into());
    }
    if raw.len() == base {
        return Ok(());
    }
    if raw.len() < 166 || raw[165] != kind {
        return Err("TOKEN2022_ACCOUNT_TYPE_OR_LENGTH".into());
    }
    if kind == 1 && raw[82..165].iter().any(|b| *b != 0) {
        return Err("TOKEN2022_MINT_PADDING".into());
    }
    let mut pos = 166;
    let mut seen = std::collections::HashSet::new();
    while pos < raw.len() {
        if raw.len() - pos < 2 {
            if raw[pos..].iter().any(|b| *b != 0) {
                return Err("TOKEN2022_TLV_TRUNCATED_TYPE".into());
            }
            break;
        }
        let tag = u16::from_le_bytes([raw[pos], raw[pos + 1]]);
        if tag == 0 {
            if raw[pos..].iter().any(|b| *b != 0) {
                return Err("TOKEN2022_TLV_NONZERO_AFTER_TERMINATOR".into());
            }
            break;
        }
        if raw.len() - pos < 4 {
            return Err("TOKEN2022_TLV_TRUNCATED_LENGTH".into());
        }
        let size = u16::from_le_bytes([raw[pos + 2], raw[pos + 3]]) as usize;
        pos += 4;
        let payload = raw
            .get(pos..pos + size)
            .ok_or("TOKEN2022_TLV_OUT_OF_BOUNDS")?;
        pos += size;
        if !seen.insert(tag) {
            return Err("TOKEN2022_DUPLICATE_EXTENSION".into());
        }
        let (expected_kind, length) = match tag {
            3 => (1, Some(32)),
            6 => (1, Some(1)),
            7 => (2, Some(0)),
            8 | 11 => (2, Some(1)),
            18 | 20 | 22 => (1, Some(64)),
            19 => (1, None),
            21 => (1, Some(80)),
            23 => (1, Some(72)),
            _ => return Err(format!("TOKEN2022_EXTENSION_UNSUPPORTED:{tag}")),
        };
        if kind != expected_kind {
            return Err("TOKEN2022_EXTENSION_WRONG_ACCOUNT_TYPE".into());
        }
        if length.is_some_and(|length| size != length) {
            return Err("TOKEN2022_EXTENSION_LENGTH".into());
        }
        if tag == 6 && payload != [1] {
            return Err("TOKEN2022_DEFAULT_ACCOUNT_STATE_NOT_INITIALIZED".into());
        }
        if (tag == 8 || tag == 11) && payload != [0] && payload != [1] {
            return Err("TOKEN2022_EXTENSION_BOOL".into());
        }
        if tag == 19 {
            metadata(payload, mint)?;
        }
        if tag == 21 && (key(payload, 32)? != mint || u64_le(payload, 64)? > u64_le(payload, 72)?) {
            return Err("TOKEN2022_GROUP_MINT_OR_SIZE".into());
        }
        if tag == 23 && key(payload, 0)? != mint {
            return Err("TOKEN2022_GROUP_MEMBER_MINT".into());
        }
    }
    Ok(())
}
pub fn mint(raw: &[u8], program: &str, address: &str, decimals: u32) -> Result<(), String> {
    if raw.len() < 82 || raw[45] != 1 {
        return Err("MINT_LAYOUT_OR_INITIALIZATION".into());
    }
    if raw[44] > 18 || raw[44] as u32 != decimals {
        return Err("MINT_DECIMALS_MISMATCH".into());
    }
    option(raw, 0)?;
    option(raw, 46)?;
    extensions(raw, program, 1, address)
}
pub fn vault(raw: &[u8], program: &str, mint_program: &str, address: &str) -> Result<u64, String> {
    if program != mint_program {
        return Err("VAULT_MINT_PROGRAM_MISMATCH".into());
    }
    if raw.len() < 165 || raw[108] != 1 || key(raw, 0)? != address {
        return Err("VAULT_LAYOUT_MINT_OR_STATE".into());
    }
    for pos in [72, 109, 129] {
        option(raw, pos)?;
    }
    extensions(raw, program, 2, address)?;
    u64_le(raw, 64)
}
/// Live `pools()` requires this revision. Empty revision is only backward
/// compatible with explicit offline decoder fixtures, not live configuration.
pub fn verify_cfg(cfg: &PoolCfg, meta: &PoolMeta) -> Result<(), String> {
    if cfg.token_contract_revision.is_empty() {
        return Ok(());
    }
    if cfg.token_contract_revision != REVISION {
        return Err("TOKEN_CONTRACT_REVISION".into());
    }
    for (address, decimals, program, mint_b64, vault_b64) in [
        (
            &meta.coin_mint,
            meta.coin_decimals,
            &cfg.coin_token_program,
            &cfg.coin_mint_data_b64,
            &cfg.coin_vault_data_b64,
        ),
        (
            &meta.pc_mint,
            meta.pc_decimals,
            &cfg.pc_token_program,
            &cfg.pc_mint_data_b64,
            &cfg.pc_vault_data_b64,
        ),
    ] {
        if !supported(program)
            || (address == WSOL && program != TOKEN)
            || (cfg.owner == RAYDIUM_AMM_V4 && program != TOKEN)
        {
            return Err("TOKEN_CONTRACT_PROGRAM".into());
        }
        let raw = STANDARD
            .decode(mint_b64)
            .map_err(|_| "TOKEN_CONTRACT_MINT_BASE64")?;
        mint(&raw, program, address, decimals)?;
        if cfg.owner != RAYDIUM_AMM_V4 {
            let raw = STANDARD
                .decode(vault_b64)
                .map_err(|_| "TOKEN_CONTRACT_VAULT_BASE64")?;
            vault(&raw, program, program, address)?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn address() -> String {
        bs58::encode([44u8; 32]).into_string()
    }
    fn base() -> Vec<u8> {
        let mut b = vec![0; 82];
        b[44] = 6;
        b[45] = 1;
        b
    }
    fn ext(tag: u16, payload: &[u8]) -> Vec<u8> {
        let mut b = base();
        b.resize(166, 0);
        b[165] = 1;
        b.extend(tag.to_le_bytes());
        b.extend((payload.len() as u16).to_le_bytes());
        b.extend(payload);
        b
    }
    #[test]
    fn legacy_mint() {
        assert!(mint(&base(), TOKEN, &address(), 6).is_ok());
    }
    #[test]
    fn t22_base() {
        assert!(mint(&base(), TOKEN_2022, &address(), 6).is_ok());
    }
    #[test]
    fn t22_metadata_pointer() {
        assert!(mint(&ext(18, &[0; 64]), TOKEN_2022, &address(), 6).is_ok());
    }
    #[test]
    fn t22_metadata() {
        let mut data = vec![0; 32];
        data.extend([44; 32]);
        data.extend([0; 16]);
        assert!(mint(&ext(19, &data), TOKEN_2022, &address(), 6).is_ok());
    }
    #[test]
    fn unknown_owner() {
        assert!(mint(&base(), "wrong", &address(), 6).is_err());
    }
    #[test]
    fn legacy_extension_rejected() {
        assert!(mint(&ext(18, &[0; 64]), TOKEN, &address(), 6).is_err());
    }
    #[test]
    fn transfer_fee_rejected_even_zero() {
        assert!(
            mint(&ext(1, &[0; 108]), TOKEN_2022, &address(), 6)
                .unwrap_err()
                .contains("UNSUPPORTED")
        );
    }
    #[test]
    fn hook_rejected() {
        assert!(mint(&ext(14, &[0; 64]), TOKEN_2022, &address(), 6).is_err());
    }
    #[test]
    fn unknown_extension() {
        assert!(mint(&ext(999, &[]), TOKEN_2022, &address(), 6).is_err());
    }
    #[test]
    fn truncated_extension() {
        let mut b = ext(18, &[0; 64]);
        b.pop();
        assert!(mint(&b, TOKEN_2022, &address(), 6).is_err());
    }
    #[test]
    fn nonzero_padding() {
        let mut b = ext(18, &[0; 64]);
        b[82] = 1;
        assert!(mint(&b, TOKEN_2022, &address(), 6).is_err());
    }
    #[test]
    fn wrong_account_type() {
        let mut b = ext(18, &[0; 64]);
        b[165] = 2;
        assert!(mint(&b, TOKEN_2022, &address(), 6).is_err());
    }
    #[test]
    fn duplicate_extension() {
        let mut b = ext(18, &[0; 64]);
        let more = b[166..].to_vec();
        b.extend(more);
        assert!(mint(&b, TOKEN_2022, &address(), 6).is_err());
    }
    #[test]
    fn metadata_wrong_mint() {
        let data = vec![0; 80];
        assert!(mint(&ext(19, &data), TOKEN_2022, &address(), 6).is_err());
    }
    #[test]
    fn frozen_default_state() {
        assert!(mint(&ext(6, &[2]), TOKEN_2022, &address(), 6).is_err());
    }
    #[test]
    fn t22_immutable_vault() {
        let mut b = vec![0; 170];
        b[..32].fill(44);
        b[108] = 1;
        b[165] = 2;
        b[166] = 7;
        assert!(vault(&b, TOKEN_2022, TOKEN_2022, &address()).is_ok());
    }
    #[test]
    fn wrong_vault_program() {
        let b = vec![0; 165];
        assert!(vault(&b, TOKEN, TOKEN_2022, &address()).is_err());
    }
    #[test]
    fn multisig_not_mint() {
        let mut b = base();
        b.resize(355, 0);
        assert!(mint(&b, TOKEN_2022, &address(), 6).is_err());
    }
}
