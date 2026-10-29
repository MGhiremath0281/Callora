import { Link } from "react-router-dom";
import { Compass } from "lucide-react";

export default function NotFoundPage() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center text-center">
      <span className="flex h-12 w-12 items-center justify-center rounded-full bg-slate-100 text-slate-500">
        <Compass className="h-5 w-5" aria-hidden />
      </span>
      <h1 className="mt-4 text-xl font-semibold text-slate-900">Page not found</h1>
      <p className="mt-1 max-w-md text-sm text-slate-500">
        The page you requested is not part of this workspace. Check the address or return
        to the overview.
      </p>
      <Link
        to="/"
        className="mt-5 inline-flex h-9 items-center justify-center rounded-lg bg-accent-600 px-4 text-sm font-medium text-white shadow-sm transition hover:bg-accent-700"
      >
        Back to overview
      </Link>
    </div>
  );
}
