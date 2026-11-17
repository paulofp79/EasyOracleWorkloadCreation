export default function Placeholder({ title, milestone }: { title: string; milestone?: string }) {
  return (
    <>
      <h1>{title}</h1>
      <div className="card muted">
        {milestone ? `This page arrives in milestone ${milestone}.` : "There is no page at this address."}
      </div>
    </>
  );
}
