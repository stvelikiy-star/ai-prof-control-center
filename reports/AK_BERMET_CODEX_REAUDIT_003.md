Final verdict: PASS

1. `current-staff.ts` selects `deleted_at` — PASS  
2. Rejects profile and role query errors — PASS  
3. Fails closed when profile is missing — PASS  
4. Requires `is_active === true` — PASS  
5. Requires `deleted_at === null` — PASS  
6. All four `/api/manager/*` routes use `isManagerAuthenticated()`, which calls corrected `getCurrentStaff()` — PASS  
7. README replaces `/manager/login` PIN access with `/staff/login` Supabase Auth — PASS  
8. Commit changes only three related files: README, resolver, and manager-session documentation — PASS  
9. Requested `git diff --check` exits successfully with code 0 — PASS

No files were modified, branches switched, packages installed, or builds run.