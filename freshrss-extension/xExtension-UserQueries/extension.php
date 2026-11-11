<?php
declare(strict_types=1);

/**
 * Lists and runs saved user queries over /api/misc.php?ext=UserQueries.
 *
 * Auth: the Google Reader API header (`Authorization: GoogleLogin auth=<user>/<token>`),
 * validated exactly like p/api/greader.php does.
 *
 * - no `name` param: {"queries": [{name, description, get, get_name, search, state, shared_rss, shared_opml}]}
 * - `name=<query name>` (+ optional nb, offset, search): the query's articles in Google Reader
 *   stream format, applied the same way as p/api/query.php (works for unshared queries too).
 */
final class UserQueriesExtension extends Minz_Extension {
	#[\Override]
	public function init(): void {
		parent::init();
		$this->registerHook(Minz_HookType::ApiMisc, [$this, 'handleApi']);
	}

	public function handleApi(): void {
		header('Content-Type: application/json; charset=UTF-8');
		header('Cache-Control: no-store');
		// misc.php does not load $_GET into Minz_Request (query.php does); needed for name/nb/offset/search.
		Minz_Request::init();
		if (self::authenticatedUser() === '') {
			self::fail(401, 'Unauthorized');
		}
		$name = Minz_Request::paramString('name', plaintext: true);
		if ($name === '') {
			self::listQueries();
		} else {
			self::runQuery($name);
		}
	}

	private static function listQueries(): void {
		$queries = [];
		foreach (FreshRSS_Context::userConf()->queries as $raw) {
			$query = new FreshRSS_UserQuery($raw, FreshRSS_Context::categories(), FreshRSS_Context::labels());
			$queries[] = [
				'name' => $query->getName(),
				'description' => $query->getDescription(),
				'get' => $query->getGet(),
				'get_name' => $query->getGetName(),
				'search' => $query->getSearch()->toString(),
				'state' => $query->getState(),
				'shared_rss' => $query->shareRss(),
				'shared_opml' => $query->shareOpml(),
			];
		}
		self::output(['queries' => $queries]);
	}

	private static function runQuery(string $name): void {
		$query = null;
		foreach (FreshRSS_Context::userConf()->queries as $raw) {
			if (($raw['name'] ?? '') === $name) {
				$query = new FreshRSS_UserQuery($raw, FreshRSS_Context::categories(), FreshRSS_Context::labels());
				break;
			}
		}
		if ($query === null) {
			self::fail(404, 'User query not found');
		}

		// Same request shaping as p/api/query.php in FreshRSS 1.30.x
		Minz_Request::_param('get', $query->getGet());
		Minz_Request::_param('order', $query->getOrder());
		if (method_exists($query, 'getSort')) {	// Newer FreshRSS only
			Minz_Request::_param('sort', $query->getSort());
		}
		Minz_Request::_param('state', (string)$query->getState());

		$search = $query->getSearch()->toString();
		try {
			// No references to other user queries in the extra search, like query.php
			$userSearch = new FreshRSS_BooleanSearch(Minz_Request::paramString('search', plaintext: true), 0, 'AND', allowUserQueries: false);
		} catch (Minz_Exception $e) {
			self::fail(400, $e->getMessage());
		}
		if ($userSearch->toString() !== '') {
			// Parenthesised and ANDed, so the extra search can only narrow the query
			$search = $search === '' ? $userSearch->toString() : $search . ' (' . $userSearch->toString() . ')';
		}
		Minz_Request::_param('search', $search);

		try {
			FreshRSS_Context::updateUsingRequest(false);
			$items = [];
			foreach (FreshRSS_index_Controller::listEntriesByContext() as $entry) {
				$entry->_feed(FreshRSS_Category::findFeed(FreshRSS_Context::categories(), $entry->feedId()));
				$items[] = $entry->toGReader('freshrss');
			}
		} catch (Minz_Exception $e) {
			self::fail(400, 'Bad user query: ' . $e->getMessage());
		}

		self::output([
			'id' => 'user/-/state/org.freshrss/query/' . rawurlencode($query->getName()),
			'title' => $query->getName(),
			'items' => $items,
		]);
	}

	/** Mirrors authorizationToUser() in p/api/greader.php. Returns '' when not authenticated. */
	private static function authenticatedUser(): string {
		$header = $_SERVER['HTTP_AUTHORIZATION'] ?? $_SERVER['REDIRECT_HTTP_AUTHORIZATION'] ?? '';
		if (!is_string($header) || $header === '') {
			$header = '';
			foreach (function_exists('getallheaders') ? getallheaders() : [] as $key => $value) {
				if (is_string($key) && strcasecmp($key, 'Authorization') === 0 && is_string($value)) {
					$header = $value;
				}
			}
		}
		// "GoogleLogin auth=<user>/<token>": PHP turns the space into "_" when parsing
		parse_str($header, $pairs);
		$auth = $pairs['GoogleLogin_auth'] ?? '';
		$parts = is_string($auth) ? explode('/', $auth, 2) : [];
		if (count($parts) !== 2 || !FreshRSS_user_Controller::checkUsername($parts[0])) {
			return '';
		}
		[$user, $token] = $parts;
		FreshRSS_Context::initUser($user);
		if (!FreshRSS_Context::hasUserConf() || !FreshRSS_Context::userConf()->enabled ||
			FreshRSS_Context::userConf()->apiPasswordHash === '') {
			return '';
		}
		$expected = sha1(FreshRSS_Context::systemConf()->salt . $user . FreshRSS_Context::userConf()->apiPasswordHash);
		return hash_equals($expected, $token) ? $user : '';
	}

	/** @param array<string,mixed> $data */
	private static function output(array $data): void {
		echo json_encode($data, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE), "\n";
	}

	private static function fail(int $code, string $message): never {
		http_response_code($code);
		self::output(['error' => $message]);
		exit();
	}
}
