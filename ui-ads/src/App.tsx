import { useMemo, useState } from 'react';

import Badge from '@atlaskit/badge';
import { DropdownItem, DropdownItemGroup, DropdownItemRadio, DropdownItemRadioGroup } from '@atlaskit/dropdown-menu';
import { DropdownMenu } from '@atlaskit/dropdown-menu/dropdown-menu';
import Flag, { FlagGroup } from '@atlaskit/flag';
import BoardIcon from '@atlaskit/icon/core/board';
import BookIcon from '@atlaskit/icon/core/book-with-bookmark';
import ChangesIcon from '@atlaskit/icon/core/changes';
import DataFlowIcon from '@atlaskit/icon/core/data-flow';
import ErrorIcon from '@atlaskit/icon/core/status-error';
import FlaskIcon from '@atlaskit/icon/core/flask';
import InboxIcon from '@atlaskit/icon/core/inbox';
import InfoIcon from '@atlaskit/icon/core/status-information';
import AutomationIcon from '@atlaskit/icon/core/automation';
import LinkExternalIcon from '@atlaskit/icon/core/link-external';
import PeopleIcon from '@atlaskit/icon/core/people-group';
import SendIcon from '@atlaskit/icon/core/send';
import SuccessIcon from '@atlaskit/icon/core/status-success';
import WarningIcon from '@atlaskit/icon/core/status-warning';
import Modal, { ModalBody, ModalHeader, ModalTitle, ModalTransition } from '@atlaskit/modal-dialog';
import { Main, Root, SideNav, SideNavBody, SideNavFooter, SideNavToggleButton, TopNav, TopNavEnd, TopNavMiddle, TopNavStart, AppLogo, CreateButton, Notifications, Profile, Search, Settings } from '@atlaskit/navigation-system';
import { Box, Stack, Text } from '@atlaskit/primitives/compiled';
import SectionMessage from '@atlaskit/section-message';
import { ExpandableMenuItem, ExpandableMenuItemContent, ExpandableMenuItemTrigger } from '@atlaskit/side-nav-items/expandable-menu-item';
import { LinkMenuItem } from '@atlaskit/side-nav-items/link-menu-item';
import { MenuList } from '@atlaskit/side-nav-items/menu-list';
import { Divider } from '@atlaskit/side-nav-items/menu-section';
import Textfield from '@atlaskit/textfield';
import { token } from '@atlaskit/tokens';

import { AlertModal } from './views/AlertModal';
import { Alerts } from './views/Alerts';
import { ConnectPlatform, PlatformPage, Platforms } from './views/Platforms';
import { Activity, Rules, Runbooks, Sent, Teams } from './views/Lists';
import { useHerald } from './store';
import { face, go, Loading, openAlert, Severity, useRoute } from './ui';

/* The product's own mark (not an Atlassian logo): a bell on the brand colour. */
function Mark({ label = 'Herald' }: { label?: string }) {
  return (
    <svg className="logo-mark" width="24" height="24" viewBox="0 0 24 24" role="img" aria-label={label}>
      <rect width="24" height="24" rx="6" fill={token('color.background.brand.bold')} />
      <path d="M12 5.5a4 4 0 0 0-4 4v2.6l-1.3 2.4h10.6L16 12.1V9.5a4 4 0 0 0-4-4Zm-1.6 10.7a1.7 1.7 0 0 0 3.2 0" fill="none" stroke={token('color.icon.inverse')} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function SearchDialog({ onClose }: { onClose: () => void }) {
  const { alerts, platforms, rules } = useHerald();
  const [q, setQ] = useState('');
  const hits = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return [];
    const has = (...texts: (string | null | undefined)[]) => texts.some((t) => (t ?? '').toLowerCase().includes(s));
    return [
      ...alerts.filter((a) => has(a.key, a.title, a.body)).slice(0, 8).map((a) => ({ id: `a${a.id}`, href: `#/alerts?alert=${a.id}`, kind: a.key, text: a.title, severity: a.severity as string | null })),
      ...platforms.filter((p) => has(p.name, p.id)).slice(0, 4).map((p) => ({ id: `p${p.id}`, href: `#/platforms/${p.id}`, kind: 'Platform', text: p.name, severity: null })),
      ...rules.filter((r) => has(r.doc.name, r.doc.id)).slice(0, 4).map((r) => ({ id: `r${r.doc.id}`, href: '#/rules', kind: 'Rule', text: r.doc.name, severity: null })),
    ];
  }, [q, alerts, platforms, rules]);
  return (
    <Modal onClose={onClose} width="medium" shouldScrollInViewport>
      <ModalHeader hasCloseButton><ModalTitle>Search</ModalTitle></ModalHeader>
      <ModalBody>
        <Stack space="space.150">
          <Textfield autoFocus aria-label="Search alerts, platforms and rules" placeholder="Alert number, title, platform or rule" value={q} onChange={(e) => setQ(e.currentTarget.value)} />
          <div className="search-results">
            {hits.map((h) => (
              <a key={h.id} className="search-hit" href={h.href} onClick={onClose}>
                <Text size="small" weight="semibold" color="color.text.subtle">{h.kind}</Text>
                <Text>{h.text}</Text>
                {h.severity && <Severity value={h.severity} />}
              </a>
            ))}
            {q.trim() && !hits.length && <Box padding="space.200"><Text color="color.text.subtle">Nothing matches “{q}”.</Text></Box>}
          </div>
          <Box paddingBlockEnd="space.200" />
        </Stack>
      </ModalBody>
    </Modal>
  );
}

const FLAG_ICON = {
  success: <SuccessIcon label="Done" color={token('color.icon.success')} />,
  error: <ErrorIcon label="Error" color={token('color.icon.danger')} />,
  warning: <WarningIcon label="Warning" color={token('color.icon.warning')} />,
  info: <InfoIcon label="Note" color={token('color.icon.information')} />,
};

export function App() {
  const h = useHerald();
  const route = useRoute();
  const [searching, setSearching] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const section = route.parts[0] ?? 'for-you';
  const mine = h.alerts.filter((a) => a.status !== 'resolved' && a.incident_manager === h.myRef).length;
  const sso = h.me.mode === 'oidc';

  const signOut = async () => {
    let next = '/login';
    try {
      const r = await fetch('/auth/logout', { method: 'POST', credentials: 'same-origin', headers: { Accept: 'application/json' } });
      const d = await r.json().catch(() => ({}));
      const url = new URL(d?.logout_url || '/login', location.origin);   // the provider's own sign-out page, when it has one
      if (/^https?:$/.test(url.protocol)) next = url.href;
    } catch {
      h.notify({ appearance: 'error', title: 'Could not sign out', description: 'Check your connection and try again.' });
      return;
    }
    location.assign(next);
  };

  const view = (() => {
    if (!h.ready) return h.offline ? null : <Loading />;
    switch (section) {
      case 'for-you': return <Alerts mine />;
      case 'alerts': return <Alerts />;
      case 'platforms': return route.parts[1] ? <PlatformPage id={route.parts[1]} tab={route.parts[2]} /> : <Platforms onConnect={() => setConnecting(true)} />;
      case 'rules': return <Rules />;
      case 'runbooks': return <Runbooks />;
      case 'teams': return <Teams />;
      case 'sent': return <Sent />;
      case 'activity': return <Activity />;
      default: return <Alerts />;
    }
  })();

  return (
    <Root>
      <TopNav>
        <TopNavStart sideNavToggleButton={<SideNavToggleButton collapseLabel="Collapse sidebar" expandLabel="Expand sidebar" />}>
          <AppLogo href="#/for-you" icon={Mark} name="Herald" label="Herald home" />
        </TopNavStart>
        <TopNavMiddle>
          <Search label="Search" onClick={() => setSearching(true)} />
          <DropdownMenu<HTMLButtonElement>
            shouldRenderToParent
            trigger={({ triggerRef, ...props }) => <CreateButton ref={triggerRef} {...props}>Create</CreateButton>}
          >
            <DropdownItemGroup>
              <DropdownItem onClick={() => setConnecting(true)} description="Send its events to Herald over a webhook">Connect a platform</DropdownItem>
              <DropdownItem href="/" description="Opens the classic interface, where the rule editor lives">New rule</DropdownItem>
              <DropdownItem href="/lab.html" description="Play a source system and watch what happens">Send a test event</DropdownItem>
            </DropdownItemGroup>
          </DropdownMenu>
        </TopNavMiddle>
        <TopNavEnd>
          <Notifications label={mine ? `${mine} open alerts assigned to you` : 'Assigned to you'} onClick={() => go('/for-you')} badge={() => (mine ? <Badge appearance="important">{mine}</Badge> : null)} />
          <DropdownMenu<HTMLButtonElement> shouldRenderToParent placement="bottom-end" trigger={({ triggerRef, ...props }) => <Settings ref={triggerRef} label="More places" {...props} />}>
            <DropdownItemGroup title="Other views">
              <DropdownItem href="/" elemAfter={<LinkExternalIcon label="" />}>Classic interface</DropdownItem>
              <DropdownItem href="/lab.html" elemAfter={<LinkExternalIcon label="" />}>Test lab</DropdownItem>
            </DropdownItemGroup>
          </DropdownMenu>
          <DropdownMenu<HTMLButtonElement> shouldRenderToParent placement="bottom-end" trigger={({ triggerRef, ...props }) => <Profile ref={triggerRef} label={`${h.me.name}: account`} src={face(h.me.name)} {...props} />}>
            <DropdownItemGroup title={`${h.me.name} · ${h.me.role}`}>
              {sso && <DropdownItem description={`Single sign-on${h.me.teams.length ? ` · team ${h.me.teams.join(', ')}` : ''}`} onClick={signOut}>Sign out</DropdownItem>}
            </DropdownItemGroup>
            {!sso && (
              <>
                <DropdownItemRadioGroup id="team" title="Viewing as team" hasSeparator>
                  {h.scopes.map((s) => (
                    <DropdownItemRadio key={s.id} id={s.id} isSelected={h.viewing.team === s.id} onClick={() => h.setViewing({ ...h.viewing, team: s.id })}>{s.name}</DropdownItemRadio>
                  ))}
                </DropdownItemRadioGroup>
                <DropdownItemRadioGroup id="role" title="In the role of" hasSeparator>
                  {([['responder', 'On-call person'], ['approver', 'Team lead (approves)'], ['editor', 'Rule editor']] as const).map(([id, label]) => (
                    <DropdownItemRadio key={id} id={id} isSelected={h.viewing.role === id} onClick={() => h.setViewing({ ...h.viewing, role: id })}>{label}</DropdownItemRadio>
                  ))}
                </DropdownItemRadioGroup>
              </>
            )}
          </DropdownMenu>
        </TopNavEnd>
      </TopNav>

      <SideNav label="Herald">
        <SideNavBody>
          <MenuList>
            <LinkMenuItem href="#/for-you" isSelected={section === 'for-you'} elemBefore={<InboxIcon label="" />} elemAfter={mine ? <Badge appearance="primary">{mine}</Badge> : undefined}>For you</LinkMenuItem>
            <LinkMenuItem href="#/alerts" isSelected={section === 'alerts'} elemBefore={<BoardIcon label="" />}>Alerts</LinkMenuItem>
            <ExpandableMenuItem isDefaultExpanded>
              <ExpandableMenuItemTrigger href="#/platforms" isSelected={section === 'platforms' && !route.parts[1]} elemBefore={<DataFlowIcon label="" />}>Platforms</ExpandableMenuItemTrigger>
              <ExpandableMenuItemContent>
                {h.platforms.map((p) => (
                  <LinkMenuItem key={p.id} href={`#/platforms/${p.id}`} isSelected={route.parts[1] === p.id}
                    elemAfter={p.firing + p.acked ? <Badge appearance={p.firing ? 'important' : 'default'}>{p.firing + p.acked}</Badge> : undefined}>
                    {p.name}
                  </LinkMenuItem>
                ))}
              </ExpandableMenuItemContent>
            </ExpandableMenuItem>
            <LinkMenuItem href="#/rules" isSelected={section === 'rules'} elemBefore={<AutomationIcon label="" />}>Rules</LinkMenuItem>
            <LinkMenuItem href="#/runbooks" isSelected={section === 'runbooks'} elemBefore={<BookIcon label="" />}>Runbooks</LinkMenuItem>
            <LinkMenuItem href="#/teams" isSelected={section === 'teams'} elemBefore={<PeopleIcon label="" />}>Teams</LinkMenuItem>
            <Divider />
            <LinkMenuItem href="#/sent" isSelected={section === 'sent'} elemBefore={<SendIcon label="" />}>Sent messages</LinkMenuItem>
            <LinkMenuItem href="#/activity" isSelected={section === 'activity'} elemBefore={<ChangesIcon label="" />}>Activity log</LinkMenuItem>
          </MenuList>
        </SideNavBody>
        <SideNavFooter>
          <MenuList>
            <LinkMenuItem href="/lab.html" elemBefore={<FlaskIcon label="" />}>Test lab</LinkMenuItem>
          </MenuList>
        </SideNavFooter>
      </SideNav>

      <Main id="main">
        {h.offline && (
          <Box padding="space.300">
            <SectionMessage appearance="warning" title="Herald is not answering">
              <Text>The page will reconnect by itself. Nothing you see below is newer than the last answer.</Text>
            </SectionMessage>
          </Box>
        )}
        <div key={route.path}>{view}</div>
      </Main>

      <ModalTransition>
        {route.alert !== null && <AlertModal id={route.alert} onClose={() => openAlert(null)} />}
      </ModalTransition>
      <ModalTransition>{searching && <SearchDialog onClose={() => setSearching(false)} />}</ModalTransition>
      <ModalTransition>{connecting && <ConnectPlatform onClose={() => setConnecting(false)} />}</ModalTransition>

      <FlagGroup onDismissed={(id) => h.dismissFlag(Number(id))}>
        {h.flags.map((f) => <Flag key={f.id} id={f.id} title={f.title} description={f.description} icon={FLAG_ICON[f.appearance]} />)}
      </FlagGroup>
    </Root>
  );
}
