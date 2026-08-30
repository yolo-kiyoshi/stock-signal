import NextAuth from "next-auth";
import GitHub from "next-auth/providers/github";

function allowedGitHubIds(): Set<string> {
  return new Set(
    (process.env.AUTH_ALLOWED_GITHUB_ID ?? "")
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean),
  );
}

export const { auth, handlers, signIn, signOut } = NextAuth({
  providers: [GitHub],
  pages: { signIn: "/sign-in" },
  session: { strategy: "jwt" },
  callbacks: {
    async signIn({ account, profile }) {
      if (account?.provider !== "github") return false;
      const profileId = String((profile as { id?: string | number } | undefined)?.id ?? "");
      const allowed = allowedGitHubIds();
      return Boolean(profileId) && allowed.size > 0 && allowed.has(profileId);
    },
  },
});
