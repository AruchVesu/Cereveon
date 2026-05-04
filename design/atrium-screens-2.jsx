// app.jsx — Cereveon design canvas
// Three coaching-screen variants + a dedicated Atrium flow (analysis, chat,
// game-end summary, onboarding). Board style is a cross-cutting tweak.

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "boardStyle": "flat"
}/*EDITMODE-END*/;

const BOARD_STYLES = [
  { value: 'flat',      label: 'Flat' },
  { value: 'engraved',  label: 'Engraved' },
  { value: 'wireframe', label: 'Wireframe' },
];

function Phone({ children, label }) {
  return (
    <AndroidDevice width={412} height={892} dark>
      <div data-screen-label={label} style={{ width: '100%', height: '100%' }}>
        {children}
      </div>
    </AndroidDevice>
  );
}

function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);

  return (
    <>
      <DesignCanvas>
        <DCSection
          id="coaching"
          title="Cereveon · In-game coaching"
          subtitle="Three hi-fi variations — dark cyberpunk × scholarly. Board style tweakable."
        >
          <DCArtboard id="obsidian" label="A · Obsidian Console" width={412} height={892}>
            <Phone label="A · Obsidian Console">
              <CoachVariantObsidian boardStyle={t.boardStyle} />
            </Phone>
          </DCArtboard>

          <DCArtboard id="atrium" label="B · Atrium (Scholarly)" width={412} height={892}>
            <Phone label="B · Atrium">
              <CoachVariantAtrium boardStyle={t.boardStyle} />
            </Phone>
          </DCArtboard>

          <DCArtboard id="hud" label="C · HUD Telemetry" width={412} height={892}>
            <Phone label="C · HUD Telemetry">
              <CoachVariantHUD boardStyle={t.boardStyle === 'flat' ? 'wireframe' : t.boardStyle} />
            </Phone>
          </DCArtboard>
        </DCSection>

        <DCSection
          id="atrium-flow"
          title="Atrium · Adjacent screens"
          subtitle="Post-move analysis, coach chat, game-end summary, onboarding — shared language."
        >
          <DCArtboard id="onboarding" label="Onboarding · skill calibration" width={412} height={892}>
            <Phone label="Atrium · Onboarding">
              <AtriumOnboarding />
            </Phone>
          </DCArtboard>

          <DCArtboard id="analysis" label="Post-move · Bg5 analysis" width={412} height={892}>
            <Phone label="Atrium · Analysis">
              <AtriumAnalysis boardStyle={t.boardStyle} />
            </Phone>
          </DCArtboard>

          <DCArtboard id="chat" label="Coach chat · dialogue" width={412} height={892}>
            <Phone label="Atrium · Chat">
              <AtriumChat />
            </Phone>
          </DCArtboard>

          <DCArtboard id="summary" label="Game end · a quiet victory" width={412} height={892}>
            <Phone label="Atrium · Summary">
              <AtriumSummary />
            </Phone>
          </DCArtboard>
        </DCSection>

        <DCSection
          id="atrium-product"
          title="Atrium · Product surface"
          subtitle="Home, curriculum, repertoire, profile, settings, paywall — same scholarly language."
        >
          <DCArtboard id="home" label="Home · library" width={412} height={892}>
            <Phone label="Atrium · Home"><AtriumHome /></Phone>
          </DCArtboard>
          <DCArtboard id="lessons" label="Lessons · curriculum" width={412} height={892}>
            <Phone label="Atrium · Lessons"><AtriumLessons /></Phone>
          </DCArtboard>
          <DCArtboard id="openings" label="Openings · repertoire" width={412} height={892}>
            <Phone label="Atrium · Openings"><AtriumOpenings /></Phone>
          </DCArtboard>
          <DCArtboard id="profile" label="Profile · stats" width={412} height={892}>
            <Phone label="Atrium · Profile"><AtriumProfile /></Phone>
          </DCArtboard>
          <DCArtboard id="settings" label="Settings · preferences" width={412} height={892}>
            <Phone label="Atrium · Settings"><AtriumSettings /></Phone>
          </DCArtboard>
          <DCArtboard id="paywall" label="Paywall · Atrium plan" width={412} height={892}>
            <Phone label="Atrium · Paywall"><AtriumPaywall /></Phone>
          </DCArtboard>
        </DCSection>
      </DesignCanvas>

      <TweaksPanel title="Tweaks">
        <TweakSection label="Board">
          <TweakRadio
            label="Style"
            value={t.boardStyle}
            options={BOARD_STYLES}
            onChange={(v) => setTweak('boardStyle', v)}
          />
        </TweakSection>
      </TweaksPanel>
    </>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
