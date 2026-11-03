from core import steam_env


def test_discovery_accounts_and_running(tmp_path):
    root = tmp_path / '.local/share/Steam'
    (root / 'userdata/123/config').mkdir(parents=True)
    (root / 'userdata/456/config').mkdir(parents=True)
    (root / 'config').mkdir()
    (root / 'config/loginusers.vdf').write_text('"users" { "76561197960265851" { "MostRecent" "1" } }')
    assert steam_env.steam_root(tmp_path) == root
    assert steam_env.accounts(root) == ['123', '456']
    assert steam_env.most_recent_account(root) == '123'
    proc = tmp_path / 'proc'
    (proc / '42').mkdir(parents=True)
    (proc / '42/comm').write_text('steam\n')
    (root / 'steam.pid').write_text('42')
    assert steam_env.steam_running(root, proc)
    (proc / '42/comm').write_text('other')
    assert not steam_env.steam_running(root, proc, tmp_path)
    (tmp_path / '.steam').mkdir()
    (tmp_path / '.steam/steam.pid').write_text('42')
    (proc / '42/comm').write_text('steam')
    assert steam_env.steam_running(root, proc, tmp_path)


def test_flatpak_and_deck(tmp_path, monkeypatch):
    (tmp_path / '.var/app/com.valvesoftware.Steam').mkdir(parents=True)
    assert steam_env.flatpak_steam_only(tmp_path)
    release = tmp_path / 'os-release'
    release.write_text('ID=steamos\n')
    monkeypatch.delenv('WLIB_STEAM_DECK', raising=False)
    assert steam_env.is_deck(release)
    monkeypatch.setenv('WLIB_STEAM_DECK', '0')
    assert not steam_env.is_deck(release)
    monkeypatch.setenv('WLIB_STEAM_DECK', '1')
    assert steam_env.is_deck(release)
    assert steam_env.effective_mode(None, True) == 'all-installed'
    assert steam_env.effective_mode(None, False) == 'off'
    assert steam_env.effective_mode('marked', True) == 'marked'
    monkeypatch.setenv('XDG_CURRENT_DESKTOP', 'gamescope')
    assert steam_env.gaming_mode()
