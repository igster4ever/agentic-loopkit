import { useState } from 'react';
import { Sidebar }      from './components/layout/Sidebar';
import { StreamsPage }  from './pages/StreamsPage';
import { EventsPage }   from './pages/EventsPage';
import { AgentsPage }   from './pages/AgentsPage';
import { AdaptersPage } from './pages/AdaptersPage';
import { ChainPage }    from './pages/ChainPage';
import { MemoryPage }   from './pages/MemoryPage';
import { LiveTail }     from './components/livetail/LiveTail';

function App() {
  const [path, setPath] = useState('/streams');

  const navigate = (href: string) => setPath(href);

  const view = (() => {
    if (path === '/streams')          return <StreamsPage />;
    if (path === '/events')           return <EventsPage onNavigate={navigate} />;
    if (path === '/live')             return <LiveTail />;
    if (path === '/agents')           return <AgentsPage />;
    if (path === '/adapters')         return <AdaptersPage />;
    if (path === '/memory')           return <MemoryPage />;
    if (path.startsWith('/chains/'))  return <ChainPage correlationId={path.slice(8)} onNavigate={navigate} />;
    return (
      <div className="p-6 text-zinc-500 text-sm">
        View <code className="text-zinc-400">{path}</code> not yet implemented.
      </div>
    );
  })();

  return (
    <div className="flex h-screen bg-zinc-950 text-zinc-100 overflow-hidden">
      <Sidebar activePath={path} onNavigate={navigate} />
      <main className="flex-1 overflow-auto">{view}</main>
    </div>
  );
}

export default App;
